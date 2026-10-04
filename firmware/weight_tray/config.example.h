// Copy to config.h and fill in. config.h is not committed.
#pragma once

#define WIFI_SSID      "care-home-wifi"
#define WIFI_PASSWORD  "change-me"

// MedVerify server (HTTPS in production).
#define MV_URL         "https://medverify.example.com/api/devices/tray-events"
#define DEVICE_ID      "TRAY-OAK-R001"
#define DEVICE_KEY     "paste-the-key-shown-once-at-registration"

// Root CA of the server's TLS certificate (PEM). Required for HTTPS.
static const char *ROOT_CA = R"(-----BEGIN CERTIFICATE-----
...
-----END CERTIFICATE-----)";

// HX711 wiring and calibration (run the calibrate sketch step in README.md).
#define HX711_DOUT_PIN 4
#define HX711_SCK_PIN  5
#define CALIBRATION_FACTOR 420.0f   // raw units per gram
