#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <HX711.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// WiFi Configuration─
const char* WIFI_SSID     = "WiFi_SSID";
const char* WIFI_PASSWORD = "WiFi_PASSWORD";

// MQTT Configuration
const char* MQTT_BROKER   = "BROKER_IP";
const int   MQTT_PORT     = 1883;
const char* CLIENT_ID     = "ESP32_TrashBin";
const char* TOPIC_REQUEST = "trash/request";
const char* TOPIC_RESULT  = "trash/result";

// HTTP Configuration
const char* FLASK_URL        = "http://<IP_ADDRESS>:5000/update";
const unsigned long VOLUME_INTERVAL_MS = 10000;   // kirim volume tiap 10 detik

// Pin Definitions
// Ultrasonic 1: deteksi objek di atas tong
#define DETECT_TRIG   5
#define DETECT_ECHO   18

// Ultrasonic 2: volume tong organik (pointing down)
#define ORG_TRIG      25
#define ORG_ECHO      26

// Ultrasonic 3: volume tong anorganik (pointing down)
#define INORG_TRIG    27
#define INORG_ECHO    14

// HX711 Load Cell
#define HX711_DOUT    19
#define HX711_SCK     21

// Servo
#define SERVO_PIN     13

// Threshold & Timing
const float  DISTANCE_THRESHOLD_CM = 20.0;   // jarak max deteksi objek (cm)
const float  WEIGHT_THRESHOLD_GRAM = 3.0;   // berat minimum sampah (gram)
const float  SCALE_CALIBRATION     = 1166.5648;    // hasil kalibrasi HX711

const unsigned long VALIDATION_DELAY_MS = 3000;    // delay sebelum request (ms)
const unsigned long SERVO_HOLD_MS       = 2000;    // lama servo terbuka (ms)
const unsigned long REQUEST_TIMEOUT_MS  = 30000;   // timeout tunggu hasil (ms)

// Volume Configuration
// EMPTY_DIST = jarak sensor ke dasar tong saat tong KOSONG (cm)
// Rumus: fillPct = ((emptyDist - currentDist) / emptyDist) * 100
const float ORG_EMPTY_DIST_CM   = 20.0;   // tinggi tong organik (cm)
const float INORG_EMPTY_DIST_CM = 20.0;   // tinggi tong anorganik (cm)

// Servo Position Angle
const int SERVO_CLOSED = 100;    // posisi tutup (tengah)
const int SERVO_RIGHT  = 180;    // organic     
const int SERVO_LEFT   = 0;   // non-organic 

// State Machine
enum State {
  IDLE,           // menunggu deteksi objek
  WAITING_DELAY,  // validasi OK, hitung mundur 3 detik
  REQUESTING,     // request terkirim, tunggu hasil MQTT
  SERVING         // servo bergerak, hitung mundur 5 detik
};

State         currentState = IDLE;
unsigned long stateTimer   = 0;

// Edge detection ultrasonic
bool prevObjectDetected = false;

// MQTT result (diisi oleh callback, dibaca oleh mqttTask)
volatile bool resultReceived = false;
String        resultClass    = "";

// Shared Volume Data (antara volumeTask → mqttTask)
SemaphoreHandle_t volumeMutex    = NULL;
float             sharedOrgPct   = 0.0;
float             sharedInorgPct = 0.0;

// Objects
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);
HX711        scale;
Servo        servo;

// Forward Declarations
void  mqttTask(void* pvParameters);
void  volumeTask(void* pvParameters);
void  connectWiFi();
void  connectMQTT();
void  mqttCallback(char* topic, byte* payload, unsigned int len);
float readDistance(int trigPin, int echoPin);
float readWeight();
float calcFillPct(float currentDist, float emptyDist);
void  sendRequest();
void  moveServo(String cls);
void  sendVolumeHTTP(float orgPct, float inorgPct);

// SETUP
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[INFO] ===== Smart Trash Bin (RTOS) Starting =====");

  // Pin Ultrasonic
  pinMode(DETECT_TRIG, OUTPUT);   pinMode(DETECT_ECHO, INPUT);
  pinMode(ORG_TRIG,    OUTPUT);   pinMode(ORG_ECHO,    INPUT);
  pinMode(INORG_TRIG,  OUTPUT);   pinMode(INORG_ECHO,  INPUT);
  Serial.println("[HC-SR04] 3 sensor ready.");

  // HX711
  scale.begin(HX711_DOUT, HX711_SCK);
  scale.set_scale(SCALE_CALIBRATION);
  scale.tare();
  Serial.println("[HX711] Ready, tared.");

  // Servo
  servo.attach(SERVO_PIN);
  servo.write(SERVO_CLOSED);
  Serial.printf("[SERVO] Ready, posisi tutup (%d°)\n", SERVO_CLOSED);

  // Mutex untuk shared volume data
  volumeMutex = xSemaphoreCreateMutex();
  if (volumeMutex == NULL) {
    Serial.println("[RTOS] ERROR: gagal buat mutex!");
    while (true);
  }

  // WiFi
  connectWiFi();

  // RTOS Tasks
  // mqttTask  : Core 1, prioritas 3 (tinggi) — MQTT + state machine
  // volumeTask: Core 0, prioritas 1 (rendah) — volume + HTTP POST
  xTaskCreatePinnedToCore(mqttTask,   "MQTT_Task",   8192,  NULL, 3, NULL, 1);
  xTaskCreatePinnedToCore(volumeTask, "Volume_Task", 10240, NULL, 1, NULL, 0);

  Serial.println("[RTOS] Tasks created.");
  Serial.println("[INFO] ===== Sistem Siap =====\n");
}

// loop
void loop() {
  vTaskDelay(portMAX_DELAY);
}

// MQTT TASK — Core 1, Prioritas 3
void mqttTask(void* pvParameters) {
  // Setup MQTT di dalam task
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  connectMQTT();

  for (;;) {
    // Jaga koneksi MQTT
    if (!mqtt.connected()) connectMQTT();
    mqtt.loop();

    // State Machine
    switch (currentState) {

      // IDLE
      case IDLE: {
        float dist = readDistance(DETECT_TRIG, DETECT_ECHO);
        bool  currDetected = (dist > 0 && dist < DISTANCE_THRESHOLD_CM);

        // Rising edge: objek baru masuk area sensor
        if (!prevObjectDetected && currDetected) {
          Serial.printf("[VALIDASI 1] OK — jarak: %.1f cm (threshold: %.0f cm)\n",
                        dist, DISTANCE_THRESHOLD_CM);

          float weight = readWeight();
          Serial.printf("[VALIDASI 2] Berat: %.1f gram (threshold: %.0f gram)\n",
                        weight, WEIGHT_THRESHOLD_GRAM);

          if (weight >= WEIGHT_THRESHOLD_GRAM) {
            Serial.println("[VALIDASI 2] OK — sampah dikonfirmasi ada.");
            Serial.printf("[INFO] Menghitung mundur %lu detik...\n",
                          VALIDATION_DELAY_MS / 1000);
            stateTimer   = millis();
            currentState = WAITING_DELAY;
            prevObjectDetected = true;
          } else {
            Serial.println("[VALIDASI 2] GAGAL — berat terlalu ringan.\n");
            prevObjectDetected = false;
          }
        } else if (!currDetected) {
          prevObjectDetected = false;
        }

        vTaskDelay(300 / portTICK_PERIOD_MS);
        break;
      }

      // WAITING_DELAY
      case WAITING_DELAY: {
        unsigned long elapsed = millis() - stateTimer;

        static unsigned long lastLog = 0;
        if (millis() - lastLog >= 1000) {
          int rem = (int)((VALIDATION_DELAY_MS - elapsed) / 1000) + 1;
          Serial.printf("[WAITING] %d detik lagi...\n", rem);
          lastLog = millis();
        }

        if (elapsed >= VALIDATION_DELAY_MS) {
          Serial.println("[INFO] Delay selesai. Mengirim request...");
          resultReceived = false;
          resultClass    = "";
          sendRequest();
          stateTimer   = millis();
          currentState = REQUESTING;
        }
        vTaskDelay(50 / portTICK_PERIOD_MS);
        break;
      }

      // REQUESTING
      case REQUESTING: {
        if (resultReceived) {
          Serial.printf("[INFO] Hasil: \"%s\"\n", resultClass.c_str());
          moveServo(resultClass);
          stateTimer   = millis();
          currentState = SERVING;
          break;
        }

        if (millis() - stateTimer >= REQUEST_TIMEOUT_MS) {
          Serial.println("[WARN] Timeout! Kembali ke IDLE.\n");
          prevObjectDetected = false;
          currentState = IDLE;
        }
        vTaskDelay(50 / portTICK_PERIOD_MS);
        break;
      }

      // SERVING
      case SERVING: {
        if (millis() - stateTimer >= SERVO_HOLD_MS) {
          servo.write(SERVO_CLOSED);
          Serial.printf("[SERVO] Posisi tutup (%d°)\n", SERVO_CLOSED);
          Serial.println("[INFO] Freeze dilepas. Siap sampah berikutnya.\n");
          prevObjectDetected = false;
          currentState = IDLE;
        }
        vTaskDelay(50 / portTICK_PERIOD_MS);
        break;
      }
    }
  }
}

// VOLUME TASK — Core 0, Prioritas 1
void volumeTask(void* pvParameters) {
  unsigned long lastSentMs = 0;

  for (;;) {
    // Kirim setiap VOLUME_INTERVAL_MS
    if (millis() - lastSentMs >= VOLUME_INTERVAL_MS) {
      lastSentMs = millis();

      // Baca sensor volume
      float orgDist   = readDistance(ORG_TRIG,   ORG_ECHO);
      float inorgDist = readDistance(INORG_TRIG, INORG_ECHO);

      // Hitung persentase
      float orgPct   = calcFillPct(orgDist,   ORG_EMPTY_DIST_CM);
      float inorgPct = calcFillPct(inorgDist, INORG_EMPTY_DIST_CM);

      Serial.printf("[VOLUME] Organik : %.1f cm → %.1f%%\n", orgDist,   orgPct);
      Serial.printf("[VOLUME] Anorganik: %.1f cm → %.1f%%\n", inorgDist, inorgPct);

      // Simpan ke shared variable (thread-safe)
      if (xSemaphoreTake(volumeMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        sharedOrgPct   = orgPct;
        sharedInorgPct = inorgPct;
        xSemaphoreGive(volumeMutex);
      }

      // HTTP POST ke Flask
      if (WiFi.status() == WL_CONNECTED) {
        sendVolumeHTTP(orgPct, inorgPct);
      } else {
        Serial.println("[HTTP] WiFi tidak terhubung, skip POST.");
      }
    }

    vTaskDelay(1000 / portTICK_PERIOD_MS);   // cek setiap 1 detik
  }
}

// WiFi
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[WiFi] Connecting to \"%s\"", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    vTaskDelay(500 / portTICK_PERIOD_MS);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected. IP: %s\n", WiFi.localIP().toString().c_str());
}

// MQTT
void connectMQTT() {
  while (!mqtt.connected()) {
    if (WiFi.status() != WL_CONNECTED) connectWiFi();

    Serial.printf("[MQTT] Connecting to %s:%d...", MQTT_BROKER, MQTT_PORT);
    if (mqtt.connect(CLIENT_ID)) {
      Serial.println(" OK");
      mqtt.subscribe(TOPIC_RESULT);
      Serial.printf("[MQTT] Subscribed: %s\n", TOPIC_RESULT);
    } else {
      Serial.printf(" FAILED (rc=%d). Retry 3s\n", mqtt.state());
      vTaskDelay(3000 / portTICK_PERIOD_MS);
    }
  }
}

// MQTT Callback
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  String message = "";
  for (unsigned int i = 0; i < len; i++) message += (char)payload[i];
  Serial.printf("[MQTT] Received [%s]: %s\n", topic, message.c_str());

  if (String(topic) != TOPIC_RESULT || currentState != REQUESTING) return;

  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, payload, len);
  if (err) {
    Serial.printf("[MQTT] JSON error: %s\n", err.c_str());
    return;
  }

  const char* cls = doc["class"];
  if (cls == nullptr) {
    Serial.println("[MQTT] ERROR: key 'class' tidak ditemukan.");
    return;
  }

  resultClass    = String(cls);
  resultReceived = true;
  Serial.printf("[MQTT] Class: \"%s\"\n", resultClass.c_str());
}

// Read Distance
float readDistance(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH, 25000);
  if (duration == 0) return -1.0;
  return (duration * 0.034f) / 2.0f;
}

// Read Weight — HX711
float readWeight() {
  if (!scale.is_ready()) {
    Serial.println("[HX711] WARNING: sensor tidak ready!");
    return 0.0;
  }
  return max(0.0f, scale.get_units(5));
}

// Hitung Persentase Volume Terisi
// Sensor di atas mengarah ke bawah:
//   Tong kosong  → currentDist ≈ emptyDist (jauh)
//   Tong penuh   → currentDist ≈ 0 (dekat)
//   fillPct = ((emptyDist - currentDist) / emptyDist) × 100
float calcFillPct(float currentDist, float emptyDist) {
  if (currentDist < 0) return 0.0;                                   // timeout sensor
  float pct = ((emptyDist - currentDist) / emptyDist) * 100.0f;
  return constrain(pct, 0.0f, 100.0f);
}

// Send MQTT Request ke laptop
void sendRequest() {
  const char* payload = "{\"request\": true}";
  bool ok = mqtt.publish(TOPIC_REQUEST, payload);
  Serial.printf("[MQTT] Published [%s]: %s — %s\n",
                TOPIC_REQUEST, payload, ok ? "OK" : "FAILED");
}

// Move Servo
void moveServo(String cls) {
  if (cls == "biological") {
    servo.write(SERVO_RIGHT);
    Serial.printf("[SERVO] ORGANIC → kanan (%d°)\n", SERVO_RIGHT);
  } else {
    servo.write(SERVO_LEFT);
    Serial.printf("[SERVO] %s → kiri (%d°)\n", cls.c_str(), SERVO_LEFT);
  }
}

// HTTP POST volume ke Flask
// Endpoint Flask:
//   @app.route('/update', methods=['POST'])
void sendVolumeHTTP(float orgPct, float inorgPct) {
  HTTPClient http;
  http.begin(FLASK_URL);
  http.addHeader("Content-Type", "application/json");

  // Buat body JSON
  char body[64];
  snprintf(body, sizeof(body),
           "{\"organic\":%.1f,\"inorganic\":%.1f}",
           orgPct, inorgPct);

  int httpCode = http.POST(body);

  if (httpCode > 0) {
    Serial.printf("[HTTP] POST → %d | body: %s\n", httpCode, body);
  } else {
    Serial.printf("[HTTP] ERROR: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}
