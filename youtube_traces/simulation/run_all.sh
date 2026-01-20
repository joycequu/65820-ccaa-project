#!/bin/bash
set -e

IMAGE_NAME="yt-network-sim"

echo "============================================"
echo "Building Simulation Docker Image: $IMAGE_NAME"
echo "============================================"
docker build -t "$IMAGE_NAME" .

###############################################
# Helper function: run inside container WITH OVS
###############################################
run_with_ovs () {
    local CMD="$1"

    docker run --privileged --rm -v "$(pwd):/app" "$IMAGE_NAME" bash -c "
        echo '>>> Initializing OVS inside container...'

        # Ensure runtime path exists
        mkdir -p /var/run/openvswitch

        # Create OVS DB if missing
        if [ ! -f /etc/openvswitch/conf.db ]; then
            ovsdb-tool create /etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema
        fi

        # Start ovsdb-server
        ovsdb-server --remote=punix:/var/run/openvswitch/db.sock \
                     --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
                     --pidfile --detach

        # Start ovs-vswitchd
        ovs-vswitchd unix:/var/run/openvswitch/db.sock --pidfile --detach

        # Verify OVS is running
        echo '>>> OVS status:'
        ovs-vsctl show || echo 'WARNING: OVS did not respond.'

        echo '>>> Running experiment command: $CMD'
        $CMD
    "
}

###############################################
# Run all scenarios
###############################################

# echo ""
# echo ">>> RUNNING SCENARIO 1: Long Form (60s) <<<"
# run_with_ovs "python3 experiment_slow.py --trace schedule/youtube-60-schedule.json --type long"

# echo ""
# echo ">>> RUNNING SCENARIO 2: Long Form (30s + Seek + 30s) <<<"
# run_with_ovs "python3 experiment_slow.py --trace schedule/youtube-scrub-schedule.json --type long"

echo ""
echo ">>> RUNNING SCENARIO 3: Shorts Doomscroll (15s x 4) <<<"
run_with_ovs 'python3 experiment.py --trace schedules/abr_doomscroll-2.json --type short'

# echo ""
# echo ">>> RUNNING SCENARIO 4: Shorts Loop (15s x 4 same video) <<<"
# run_with_ovs 'python3 experiment_slow.py --trace schedule/ys-repeat-schedule.json --type short'

echo ""
echo ">>> RUNNING SCENARIO 5: Shorts Rapid Scroll (2s x 30) <<<"
run_with_ovs 'python3 experiment.py --trace schedules/abr_rapid-1.json --type short'

echo ""
echo "============================================"
echo "All scenarios completed successfully."
echo "Results saved to results.csv (if CSV export enabled)."
echo "============================================"