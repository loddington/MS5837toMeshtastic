#pragma once

#include "../../../detect/ScanI2C.h"
#include "TelemetrySensor.h"
#include <MS5837.h>

/**
 * MS5837-30BA high-resolution underwater pressure & temperature sensor.
 *
 * I2C address: 0x76 (fixed, no alternatives)
 * Pressure range: 0–300 bar  (30BA variant)
 * Resolution: 0.2 mbar  →  ~2 mm water depth
 *
 * Reports via EnvironmentMetrics:
 *   barometricPressure  – absolute pressure in hPa (mbar)
 *   temperature         – water/fluid temperature in °C
 *   distance            – calculated water depth in mm
 *                         (requires TANK_SURFACE_PRESSURE_HPA calibration,
 *                          see MS5837Sensor.cpp)
 */
class MS5837Sensor : public TelemetrySensor
{
  private:
    MS5837 sensor;

    // Atmospheric pressure at the tank surface (hPa).
    // Default is standard sea-level pressure (1013.25 hPa).
    // Override this at runtime via the `distance_offset_mm` telemetry
    // calibration config, OR hard-code your local value here if the
    // tank is always in the same environment.
    float surfacePressureHpa = 1013.25f;

    // Density of the fluid in the tank (kg/m³).
    // Fresh water ≈ 997, salt water ≈ 1025.
    static constexpr float FLUID_DENSITY_KG_M3 = 997.0f;

    // Physical height of the tank in metres – used for a sanity-clip on
    // the depth reading so we never report impossible values.
    static constexpr float TANK_HEIGHT_M = 4.0f;

    // Height of the sensor above the tank floor in mm.
    // Sensor is raised 100mm off the floor to keep it clear of sediment.
    // This offset is added to all depth readings so the reported value
    // reflects true water level from the tank floor.
    static constexpr float SENSOR_FLOOR_OFFSET_MM = 100.0f;

  public:
    MS5837Sensor();
    virtual bool initDevice(TwoWire *bus, ScanI2C::FoundDevice *dev) override;
    virtual bool getMetrics(meshtastic_Telemetry *measurement) override;
};
