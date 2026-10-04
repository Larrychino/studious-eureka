# Weight tray firmware

One tray per resident with fridge medicines. Bill of materials (prototype, roughly £15–£25 per tray):

| Part | Notes |
|------|-------|
| ESP32 dev board (e.g. ESP32-C3 mini) | Wi-Fi, deep sleep capable |
| 1 kg or 5 kg bar load cell | 1 kg gives better resolution for pens and eye drops |
| HX711 amplifier board | 10 Hz mode |
| Food-safe tray + base | Washable tray must lift off the cell for cleaning |
| USB-C 5 V supply | Fridges have no battery-friendly temperatures for Li-ion; power from outside the fridge via a flat cable |

## Build and flash

1. Install the ESP32 Arduino core and the `HX711` library by Bogdan Necula.
2. `cp config.example.h config.h` and fill in Wi-Fi, server URL, device ID and the key shown once when a manager registers the device (Settings → Register a device).
3. Calibrate: with the empty tray, note `scale.read_average(20)` as the offset; put a known 100 g weight on and set `CALIBRATION_FACTOR = (reading - offset) / 100`.
4. Flash `weight_tray.ino`.

## Bench test before any home (plan, Phase 2)

Run for two weeks at home with dummy items. Check in the app (Stock & MAR → Physical vs recorded) that every removal and return appears, and that no events are invented by temperature drift inside the fridge (load cells drift with cold; re-check the noise threshold at 4 °C, not room temperature).
