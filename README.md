# MS5837toMeshtastic
Add the MS5837 sensor (GW-MS5837-30BA) to meshtastic for water level measurement.  

# 1. Pull the new firmware
cd meshtastic/firmware
git pull
git submodule update --init --recursive

# 2. Run the patch script (from the firmware root)
python patch_ms5837.py --surface-pressure 1025.0

# 3. Build
pio run -e nrf52_promicro_diy_tcxo

# Check if patch is already applied
python patch_ms5837.py --check

# Preview changes without writing anything
python patch_ms5837.py --dry-run

# Apply to a specific directory
python patch_ms5837.py --firmware-dir C:/Dev/meshtastic/firmware --surface-pressure 1025.0

