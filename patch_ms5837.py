#!/usr/bin/env python3
"""
patch_ms5837.py — Apply MS5837-30BA water tank level sensor support
                  to a fresh Meshtastic firmware repository.

Usage:
    python patch_ms5837.py [--firmware-dir PATH] [--surface-pressure HPA]
                           [--tank-height M] [--floor-offset MM]
                           [--dry-run] [--check]

Arguments:
    --firmware-dir PATH       Path to the meshtastic/firmware repo root.
                              Defaults to the current working directory.
    --surface-pressure HPA    Atmospheric pressure at tank surface in hPa.
                              Default: 1013.25 (standard sea level).
                              Tip: read your local pressure from a weather
                              station, or read the sensor in open air first.
    --tank-height M           Physical tank height in metres (sanity clip).
                              Default: 4.0
    --floor-offset MM         Height of sensor above tank floor in mm.
                              Default: 100.0
    --dry-run                 Print what would be changed without writing.
    --check                   Verify patch has already been applied correctly.

Examples:
    # Apply with defaults (run from inside the firmware repo):
    python patch_ms5837.py

    # Apply with local surface pressure calibration:
    python patch_ms5837.py --surface-pressure 1025.0

    # Apply to a specific directory:
    python patch_ms5837.py --firmware-dir C:/Dev/meshtastic/firmware

    # Check if patch is already applied:
    python patch_ms5837.py --check
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from textwrap import dedent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class PatchError(Exception):
    pass


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PatchError(f"File not found: {path}\n"
                         "Make sure --firmware-dir points to the root of the "
                         "meshtastic/firmware repository.")


def write_file(path: Path, content: str, dry_run: bool):
    if dry_run:
        print(f"  [DRY RUN] Would write {path}")
    else:
        path.write_text(content, encoding="utf-8")
        print(f"  Written:  {path}")


def apply_replacement(content: str, old: str, new: str, description: str) -> str:
    """Replace old with new exactly once; raise PatchError if not found."""
    old = dedent(old).strip()
    new = dedent(new).strip()
    if old not in content:
        raise PatchError(
            f"Could not find anchor text for: {description}\n"
            "The firmware source may have changed since this patch was written.\n"
            "Search for the anchor text below and apply the change manually:\n\n"
            f"{old}"
        )
    count = content.count(old)
    if count > 1:
        raise PatchError(
            f"Anchor text for '{description}' matched {count} times (expected 1). "
            "Cannot apply safely."
        )
    return content.replace(old, new, 1)


def already_patched(content: str, marker: str) -> bool:
    return marker in content


# ---------------------------------------------------------------------------
# New sensor files
# ---------------------------------------------------------------------------

def ms5837_sensor_h(surface_pressure: float, tank_height: float,
                    floor_offset: float) -> str:
    return f"""\
#pragma once

#include "../../../detect/ScanI2C.h"
#include "TelemetrySensor.h"
#include <MS5837.h>

/**
 * MS5837-30BA high-resolution underwater pressure & temperature sensor.
 *
 * I2C address: 0x76 (fixed, no alternatives)
 * Pressure range: 0-300 bar  (30BA variant)
 * Resolution: 0.2 mbar  ~2 mm water depth
 *
 * Reports via EnvironmentMetrics:
 *   barometricPressure  - absolute pressure in hPa (mbar)
 *   temperature         - water/fluid temperature in degrees C
 *   distance            - calculated water depth in mm
 */
class MS5837Sensor : public TelemetrySensor
{{
  private:
    MS5837 sensor;

    // Atmospheric pressure at the tank surface (hPa).
    // Set to your local atmospheric pressure for accurate depth readings.
    // Read the sensor in open air and use that value, or use a local
    // weather station reading.
    float surfacePressureHpa = {surface_pressure:.2f}f;

    // Density of the fluid in the tank (kg/m3).
    // Fresh water ~997, salt water ~1025.
    static constexpr float FLUID_DENSITY_KG_M3 = 997.0f;

    // Physical height of the tank in metres - used as a sanity-clip on
    // the depth reading so we never report impossible values.
    static constexpr float TANK_HEIGHT_M = {tank_height:.1f}f;

    // Height of the sensor above the tank floor in mm.
    // Raise the sensor off the floor to keep it clear of sediment.
    // This offset is added to all depth readings so the reported value
    // reflects true water level from the tank floor.
    static constexpr float SENSOR_FLOOR_OFFSET_MM = {floor_offset:.1f}f;

  public:
    MS5837Sensor();
    virtual bool initDevice(TwoWire *bus, ScanI2C::FoundDevice *dev) override;
    virtual bool getMetrics(meshtastic_Telemetry *measurement) override;
}};
"""


def ms5837_sensor_cpp() -> str:
    return """\
#include "MS5837Sensor.h"
#include "configuration.h"

// --------------------------------------------------------------------------
// Physics helper
// --------------------------------------------------------------------------
// Depth (m) = delta_P (Pa) / (rho x g)
//   delta_P = (absolute_pressure_hPa - surface_pressure_hPa) x 100  [Pa]
//   rho     = fluid density (kg/m3)
//   g       = 9.80665 m/s2
static constexpr float GRAVITY_MS2 = 9.80665f;

// --------------------------------------------------------------------------
// Constructor
// --------------------------------------------------------------------------
MS5837Sensor::MS5837Sensor()
    : TelemetrySensor(meshtastic_TelemetrySensorType_SENSOR_UNSET, "MS5837")
{
}

// --------------------------------------------------------------------------
// initDevice - called by addSensor<> template after I2C detection.
// Receives the I2C bus pointer directly. Must return true to be added
// to the active sensors list, false to be discarded.
// --------------------------------------------------------------------------
bool MS5837Sensor::initDevice(TwoWire *bus, ScanI2C::FoundDevice *dev)
{
#if WIRE_INTERFACES_COUNT == 2
    LOG_INFO("[MS5837] Initialising sensor on I2C bus %s\\n", (bus == &Wire1) ? "Wire1" : "Wire");
#else
    LOG_INFO("[MS5837] Initialising sensor on I2C bus Wire\\n");
#endif

    // Lower I2C clock to 100kHz for reliable operation over long cables.
    // The default 400kHz (fast mode) causes signal integrity issues due
    // to capacitive loading on SDA/SCL over cable runs of 1m or more.
    bus->setClock(100000);

    if (!sensor.init(*bus)) {
        LOG_WARN("[MS5837] Sensor not found / failed CRC check - check wiring.\\n");
        return false;
    }

    // Tell the library which variant we have so it uses the correct
    // second-order compensation formula.
    sensor.setModel(MS5837::MS5837_30BA);

    // Fluid density for depth calculation.
    sensor.setFluidDensity(FLUID_DENSITY_KG_M3);

    LOG_INFO("[MS5837] Sensor initialised OK.\\n");
    status = 1;
    initialized = true;
    return true;
}

// --------------------------------------------------------------------------
// getMetrics - read sensor and populate the telemetry struct.
// --------------------------------------------------------------------------
bool MS5837Sensor::getMetrics(meshtastic_Telemetry *measurement)
{
    if (status == 0) {
        LOG_WARN("[MS5837] getMetrics called but sensor not initialised.\\n");
        return false;
    }

    // Trigger a conversion and block until the result is ready.
    // The library handles the mandatory ~9 ms ADC delay internally.
    sensor.read();

    float pressureHpa  = sensor.pressure();   // absolute pressure in hPa (mbar)
    float temperatureC = sensor.temperature(); // degrees C

    // Guard against obviously bad readings (sensor not submerged, wiring
    // fault, etc.).
    if (pressureHpa < 800.0f || pressureHpa > 31000.0f) {
        LOG_WARN("[MS5837] Pressure reading out of range: %.2f hPa\\n", pressureHpa);
        return false;
    }

    // ------------------------------------------------------------------
    // Depth calculation
    // ------------------------------------------------------------------
    // delta_P must be positive; if the sensor reads lower than the surface
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
    depthMm += SENSOR_FLOOR_OFFSET_MM;

    // ------------------------------------------------------------------
    // Fill in the protobuf fields
    //   barometricPressure  -> raw pressure in hPa
    //   temperature         -> fluid temperature in degrees C
    //   distance            -> depth in mm
    //     (re-purposing the RCWL-9620 "distance" field; the Meshtastic
    //      app and dashboard both display it labelled as "Distance / mm")
    // ------------------------------------------------------------------
    measurement->variant.environment_metrics.barometric_pressure = pressureHpa;
    measurement->variant.environment_metrics.temperature          = temperatureC;
    measurement->variant.environment_metrics.distance             = depthMm;
    measurement->variant.environment_metrics.has_barometric_pressure = true;
    measurement->variant.environment_metrics.has_temperature         = true;
    measurement->variant.environment_metrics.has_distance            = true;

    LOG_INFO("[MS5837] P=%.2f hPa  T=%.2f degC  depth=%.1f mm\\n",
             pressureHpa, temperatureC, depthMm);

    return true;
}
"""


# ---------------------------------------------------------------------------
# Per-file patches
# ---------------------------------------------------------------------------

PATCH_MARKER = "// MS5837_PATCH_APPLIED"


def patch_scan_i2c_h(content: str) -> str:
    """Add MS5837 to the DeviceType enum."""
    return apply_replacement(
        content,
        old="BMP280,\n        BME280,",
        new="BMP280,\n        BME280,\n        MS5837,",
        description="ScanI2C.h DeviceType enum"
    )


def patch_scan_i2c_two_wire_cpp(content: str) -> str:
    """Add MS5837 detection inside the case 0x00 branch of the BME_ADDR block."""
    old = """\
                    if (type == DPS310) {
                        break;
                    }
                default:"""
    new = """\
                    if (type == DPS310) {
                        break;
                    }
                    // Check for MS5837: send RESET command (0x1E) - only MS5837 will ACK
                    // this at address 0x76 when the Bosch chip-ID register returned 0x00.
                    if (addr.address == 0x76) {
                        wire->beginTransmission(addr.address);
                        wire->write(0x1E); // MS5837 RESET command
                        if (wire->endTransmission() == 0) {
                            delay(10); // wait for reset to complete
                            wire->beginTransmission(addr.address);
                            wire->write(0xA0); // PROM READ C0
                            if (wire->endTransmission() == 0) {
                                wire->requestFrom(addr.address, (uint8_t)2);
                                if (wire->available() >= 2) {
                                    logFoundDevice("MS5837", (uint8_t)addr.address);
                                    type = MS5837;
                                }
                            }
                        }
                    }
                    if (type == MS5837) {
                        break;
                    }
                default:"""
    return apply_replacement(content, old, new, "ScanI2CTwoWire.cpp MS5837 detection")


def patch_environment_telemetry_cpp(content: str) -> str:
    """Add MS5837 include and addSensor call."""
    # Include
    content = apply_replacement(
        content,
        old='#include "Sensor/RCWL9620Sensor.h"\n#include "Sensor/nullSensor.h"',
        new='#include "Sensor/RCWL9620Sensor.h"\n'
            '#include "Sensor/nullSensor.h"\n\n'
            '#if __has_include(<MS5837.h>)\n'
            '#include "Sensor/MS5837Sensor.h"\n'
            '#endif',
        description="EnvironmentTelemetry.cpp MS5837 include"
    )
    # addSensor call
    content = apply_replacement(
        content,
        old='addSensor<RCWL9620Sensor>(i2cScanner, ScanI2C::DeviceType::RCWL9620);\n'
            '    addSensor<CGRadSensSensor>(i2cScanner, ScanI2C::DeviceType::CGRADSENS);',
        new='addSensor<RCWL9620Sensor>(i2cScanner, ScanI2C::DeviceType::RCWL9620);\n'
            '    addSensor<CGRadSensSensor>(i2cScanner, ScanI2C::DeviceType::CGRADSENS);\n'
            '#if __has_include(<MS5837.h>)\n'
            '    addSensor<MS5837Sensor>(i2cScanner, ScanI2C::DeviceType::MS5837);\n'
            '#endif',
        description="EnvironmentTelemetry.cpp addSensor call"
    )
    return content


def patch_platformio_ini(content: str) -> str:
    """Add BlueRobotics MS5837 library dependency."""
    # Find the lib_deps line and append after the last entry before the next blank/section
    if "bluerobotics/BlueRobotics_MS5837_Library" in content:
        return content  # already present
    # Insert after adafruit/Adafruit BME280 Library which is reliably present
    return apply_replacement(
        content,
        old="adafruit/Adafruit BME280 Library",
        new="adafruit/Adafruit BME280 Library\n"
            "    bluerobotics/BlueRobotics_MS5837_Library @ ^1.1.1",
        description="platformio.ini BlueRobotics MS5837 library"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Apply MS5837-30BA water tank sensor patch to Meshtastic firmware."
    )
    parser.add_argument(
        "--firmware-dir", default=".",
        help="Path to meshtastic/firmware repo root (default: current directory)"
    )
    parser.add_argument(
        "--surface-pressure", type=float, default=1013.25,
        help="Atmospheric pressure at tank surface in hPa (default: 1013.25). "
             "Tip: read sensor in open air first and use that value."
    )
    parser.add_argument(
        "--tank-height", type=float, default=4.0,
        help="Physical tank height in metres (default: 4.0)"
    )
    parser.add_argument(
        "--floor-offset", type=float, default=100.0,
        help="Sensor height above tank floor in mm (default: 100.0)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be changed without writing any files"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check whether patch has already been applied"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    fw = Path(args.firmware_dir).resolve()

    print(f"\nMeshtastic MS5837-30BA Patch Tool")
    print(f"{'=' * 50}")
    print(f"Firmware directory : {fw}")
    print(f"Surface pressure   : {args.surface_pressure:.2f} hPa")
    print(f"Tank height        : {args.tank_height:.1f} m")
    print(f"Floor offset       : {args.floor_offset:.1f} mm")
    if args.dry_run:
        print(f"Mode               : DRY RUN (no files will be written)")
    elif args.check:
        print(f"Mode               : CHECK ONLY")
    print()

    # Define all paths
    paths = {
        "scan_i2c_h":           fw / "src/detect/ScanI2C.h",
        "scan_i2c_twowire_cpp": fw / "src/detect/ScanI2CTwoWire.cpp",
        "environment_cpp":      fw / "src/modules/Telemetry/EnvironmentTelemetry.cpp",
        "platformio_ini":       fw / "platformio.ini",
        "sensor_h":             fw / "src/modules/Telemetry/Sensor/MS5837Sensor.h",
        "sensor_cpp":           fw / "src/modules/Telemetry/Sensor/MS5837Sensor.cpp",
    }

    # ------------------------------------------------------------------
    # CHECK mode
    # ------------------------------------------------------------------
    if args.check:
        all_good = True
        checks = [
            (paths["scan_i2c_h"],           "MS5837,",                        "ScanI2C.h DeviceType"),
            (paths["scan_i2c_twowire_cpp"],  "MS5837 RESET command",           "ScanI2CTwoWire.cpp detection"),
            (paths["environment_cpp"],       "MS5837Sensor.h",                 "EnvironmentTelemetry.cpp include"),
            (paths["environment_cpp"],       "addSensor<MS5837Sensor>",        "EnvironmentTelemetry.cpp addSensor"),
            (paths["sensor_h"],              "class MS5837Sensor",             "MS5837Sensor.h"),
            (paths["sensor_cpp"],            "bool MS5837Sensor::initDevice",  "MS5837Sensor.cpp"),
        ]
        for path, marker, label in checks:
            if path.exists() and marker in read_file(path):
                print(f"  [OK]     {label}")
            else:
                print(f"  [MISSING] {label}  ({path.name})")
                all_good = False
        print()
        if all_good:
            print("Patch is fully applied.")
        else:
            print("Patch is NOT fully applied. Run without --check to apply.")
        return 0 if all_good else 1

    # ------------------------------------------------------------------
    # Verify firmware root looks correct
    # ------------------------------------------------------------------
    if not (fw / "platformio.ini").exists():
        print(f"ERROR: {fw}/platformio.ini not found.")
        print("Make sure --firmware-dir points to the root of the meshtastic/firmware repo.")
        return 1

    errors = []

    # ------------------------------------------------------------------
    # 1. Copy new sensor files
    # ------------------------------------------------------------------
    print("Step 1: Write new sensor files")
    write_file(paths["sensor_h"],
               ms5837_sensor_h(args.surface_pressure, args.tank_height, args.floor_offset),
               args.dry_run)
    write_file(paths["sensor_cpp"],
               ms5837_sensor_cpp(),
               args.dry_run)

    # ------------------------------------------------------------------
    # 2. Patch ScanI2C.h
    # ------------------------------------------------------------------
    print("\nStep 2: Patch src/detect/ScanI2C.h")
    try:
        content = read_file(paths["scan_i2c_h"])
        if already_patched(content, "MS5837,"):
            print("  Already patched, skipping.")
        else:
            content = patch_scan_i2c_h(content)
            write_file(paths["scan_i2c_h"], content, args.dry_run)
    except PatchError as e:
        print(f"  WARNING: {e}")
        errors.append("ScanI2C.h")

    # ------------------------------------------------------------------
    # 3. Patch ScanI2CTwoWire.cpp
    # ------------------------------------------------------------------
    print("\nStep 3: Patch src/detect/ScanI2CTwoWire.cpp")
    try:
        content = read_file(paths["scan_i2c_twowire_cpp"])
        if already_patched(content, "MS5837 RESET command"):
            print("  Already patched, skipping.")
        else:
            content = patch_scan_i2c_two_wire_cpp(content)
            write_file(paths["scan_i2c_twowire_cpp"], content, args.dry_run)
    except PatchError as e:
        print(f"  WARNING: {e}")
        errors.append("ScanI2CTwoWire.cpp")

    # ------------------------------------------------------------------
    # 4. Patch EnvironmentTelemetry.cpp
    # ------------------------------------------------------------------
    print("\nStep 4: Patch src/modules/Telemetry/EnvironmentTelemetry.cpp")
    try:
        content = read_file(paths["environment_cpp"])
        if already_patched(content, "addSensor<MS5837Sensor>"):
            print("  Already patched, skipping.")
        else:
            content = patch_environment_telemetry_cpp(content)
            write_file(paths["environment_cpp"], content, args.dry_run)
    except PatchError as e:
        print(f"  WARNING: {e}")
        errors.append("EnvironmentTelemetry.cpp")

    # ------------------------------------------------------------------
    # 5. Patch platformio.ini
    # ------------------------------------------------------------------
    print("\nStep 5: Patch platformio.ini")
    try:
        content = read_file(paths["platformio_ini"])
        if already_patched(content, "BlueRobotics_MS5837_Library"):
            print("  Already patched, skipping.")
        else:
            content = patch_platformio_ini(content)
            write_file(paths["platformio_ini"], content, args.dry_run)
    except PatchError as e:
        print(f"  WARNING: {e}")
        errors.append("platformio.ini")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    if errors:
        print(f"Patch completed with {len(errors)} manual step(s) required:")
        for f in errors:
            print(f"  - {f} could not be patched automatically.")
            print(f"    See the WARNING above for the anchor text to search for.")
        print()
        print("All other files were patched successfully.")
        print("After fixing the manual steps, build with:")
    else:
        print("Patch applied successfully!")
        print()
        if args.surface_pressure == 1013.25:
            print("TIP: For best accuracy, re-run with your local atmospheric pressure:")
            print("     python patch_ms5837.py --surface-pressure <YOUR_HPA>")
            print("     (Read the sensor in open air first to get this value.)")
            print()
        print("Now build with:")

    print("    pio run -e nrf52_promicro_diy_tcxo")
    print()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
