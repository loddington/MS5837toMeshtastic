#include "MS5837Sensor.h"
#include "configuration.h"

// --------------------------------------------------------------------------
// Physics helper
// --------------------------------------------------------------------------
// Depth (m) = ΔP (Pa) / (ρ × g)
//   ΔP  = (absolute_pressure_hPa - surface_pressure_hPa) × 100  [Pa]
//   ρ   = fluid density (kg/m³)
//   g   = 9.80665 m/s²
static constexpr float GRAVITY_MS2 = 9.80665f;

// --------------------------------------------------------------------------
// Constructor
// --------------------------------------------------------------------------
MS5837Sensor::MS5837Sensor()
    : TelemetrySensor(meshtastic_TelemetrySensorType_SENSOR_UNSET, "MS5837")
{
}

// --------------------------------------------------------------------------
// initDevice – called by addSensor<> template after I2C detection.
// Receives the I2C bus pointer directly. Must return true to be added
// to the active sensors list, false to be discarded.
// --------------------------------------------------------------------------
bool MS5837Sensor::initDevice(TwoWire *bus, ScanI2C::FoundDevice *dev)
{
#if WIRE_INTERFACES_COUNT == 2
    LOG_INFO("[MS5837] Initialising sensor on I2C bus %s\n", (bus == &Wire1) ? "Wire1" : "Wire");
#else
    LOG_INFO("[MS5837] Initialising sensor on I2C bus Wire\n");
#endif

    // Lower I2C clock to 100kHz for reliable operation over a 4m cable.
    // The default 400kHz (fast mode) causes signal integrity issues at this
    // cable length due to capacitive loading on SDA/SCL.
    bus->setClock(100000);

    if (!sensor.init(*bus)) {
        LOG_WARN("[MS5837] Sensor not found / failed CRC check - check wiring.\n");
        return false;
    }

    // Tell the library which variant we have so it uses the correct
    // second-order compensation formula.
    sensor.setModel(MS5837::MS5837_30BA);

    // Fluid density for depth calculation.
    sensor.setFluidDensity(FLUID_DENSITY_KG_M3);

    LOG_INFO("[MS5837] Sensor initialised OK.\n");
    status = 1;
    initialized = true;
    return true;
}

// --------------------------------------------------------------------------
// getMetrics – read sensor and populate the telemetry struct.
// --------------------------------------------------------------------------
bool MS5837Sensor::getMetrics(meshtastic_Telemetry *measurement)
{
    if (status == 0) {
        LOG_WARN("[MS5837] getMetrics called but sensor not initialised.\n");
        return false;
    }

    // Trigger a conversion and block until the result is ready.
    // The library handles the mandatory ~9 ms ADC delay internally.
    sensor.read();

    float pressureHpa  = sensor.pressure();   // absolute pressure in hPa (mbar)
    float temperatureC = sensor.temperature(); // °C

    // Guard against obviously bad readings (sensor not submerged, wiring
    // fault, etc.).
    if (pressureHpa < 800.0f || pressureHpa > 31000.0f) {
        LOG_WARN("[MS5837] Pressure reading out of range: %.2f hPa\n", pressureHpa);
        return false;
    }

    // ------------------------------------------------------------------
    // Depth calculation
    // ------------------------------------------------------------------
    // ΔP must be positive; if the sensor reads lower than the surface
    // reference it simply means it is at or above the water surface.
    float deltaPressurePa = (pressureHpa - surfacePressureHpa) * 100.0f;
    float depthM = (deltaPressurePa > 0.0f)
                       ? (deltaPressurePa / (FLUID_DENSITY_KG_M3 * GRAVITY_MS2))
                       : 0.0f;

    // Sanity-clip to the physical tank height.
    if (depthM > TANK_HEIGHT_M) {
        depthM = TANK_HEIGHT_M;
    }

    float depthMm = depthM * 1000.0f;

    // Add the physical height of the sensor above the tank floor.
    // The sensor is mounted 100mm off the floor to keep it clear of sediment,
    // so we add this offset so the reported value reflects true tank level
    // from the bottom rather than depth from the sensor face.
    depthMm += SENSOR_FLOOR_OFFSET_MM;

    // ------------------------------------------------------------------
    // Fill in the protobuf fields
    //   barometricPressure  → raw pressure in hPa
    //   temperature         → fluid temperature in °C
    //   distance            → depth in mm
    //     (re-purposing the RCWL-9620 "distance" field; the Meshtastic
    //      app and dashboard both display it labelled as "Distance / mm")
    // ------------------------------------------------------------------
    measurement->variant.environment_metrics.barometric_pressure = pressureHpa;
    measurement->variant.environment_metrics.temperature          = temperatureC;
    measurement->variant.environment_metrics.distance             = depthMm;
    measurement->variant.environment_metrics.has_barometric_pressure = true;
    measurement->variant.environment_metrics.has_temperature         = true;
    measurement->variant.environment_metrics.has_distance            = true;

    LOG_INFO("[MS5837] P=%.2f hPa  T=%.2f°C  depth=%.1f mm\n",
             pressureHpa, temperatureC, depthMm);

    return true;
}
