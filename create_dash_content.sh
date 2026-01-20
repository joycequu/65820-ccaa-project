#!/bin/bash

# Define variables
SOURCE_FILE="input.mp4"
AUDIO_BITRATE="128k"
SEGMENT_FRAMES=60 # 2 seconds at 30 FPS

# Video ladder configurations: BITRATE, RESOLUTION
# Ladder covers Low, Medium, and High quality profiles
PROFILES=(
    "500k:640:360" # Low Quality
    "1500k:1280:720" # Medium Quality
    "3000k:1920:1080" # High Quality
)

# Output file list for manifest creation
VIDEO_FILES=()

# Check for file
if [ ! -f "$SOURCE_FILE" ]; then
    echo "Error: Source file '$SOURCE_FILE' not found."
    exit 1
fi

echo "--- Starting DASH Content Generation ---"

# Create Audio Track
echo "Creating Audio Track (128 kbps)..."
ffmpeg -i "$SOURCE_FILE" -vn -c:a aac -b:a "$AUDIO_BITRATE" audio.m4a
if [ $? -ne 0 ]; then echo "Error encoding audio. Aborting."; exit 1; fi

# Create Video Tracks
echo "Creating Video Tracks (ABR Ladder)..."
STREAM_INDEX=0
VIDEO_INPUTS=""

for PROFILE in "${PROFILES[@]}"; do
    IFS=":" read -r BITRATE RESOLUTION_W RESOLUTION_H <<< "$PROFILE"
    RESOLUTION="${RESOLUTION_W}:${RESOLUTION_H}"
    OUTPUT_FILE="video_${RESOLUTION_H}p_${BITRATE}.mp4"
    VIDEO_FILES+=("$OUTPUT_FILE")

    echo "    - ENCODING ${RESOLUTION} at ${BITRATE} to ${OUTPUT_FILE}..."

    ffmpeg -i "$SOURCE_FILE" -an -c:v libx264 -preset veryfast -keyint_min "$SEGMENT_FRAMES" -g "$SEGMENT_FRAMES" \
    -vf "scale=${RESOLUTION}" -b:v "$BITRATE" -f dash "$OUTPUT_FILE"
    
    if [ $? -ne 0 ]; then echo "Error encoding video profile. Aborting."; exit 1; fi

    VIDEO_INPUTS+="-i $OUTPUT_FILE "
done

# Create DASH Manifest
echo "Creating the final DASH Manifest (manifest.mpd)..."

ffmpeg $VIDEO_INPUTS -i audio.m4a \
    -c copy \
    -map 0 -map 1 -map 2 -map 3 \
    -f dash manifest.mpd
    
if [ $? -ne 0 ]; then echo "Error creating manifest. Aborting."; exit 1; fi

echo "--- DASH Content Generation Complete ---"
echo "Generated files: manifest.mpd and associated .mp4 files."