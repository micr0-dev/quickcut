import re
import sys


def parse_silence_log(log_file):
    segments = []
    start = None
    duration = None

    with open(log_file, "r") as f:
        for line in f:
            if "silence_start: " in line:
                parts = line.split("silence_start: ")
                start = float(parts[1])
            elif "silence_duration: " in line and start is not None:
                parts = line.split("silence_duration: ")
                duration = float(parts[1])
                segments.append((start, start + duration))
                start = None  # Reset start to capture the next pair

    if not segments:
        print("No silence segments detected.")
        return None

    # Create intervals of sound from the detected silence segments
    intervals = []
    prev_end = 0.0
    for start, end in segments:
        intervals.append((prev_end, start))
        prev_end = end

    # Add the final interval to the end of the video
    intervals.append((prev_end, None))
    return intervals


def generate_ffmpeg_trim_command(input_file, intervals, output_file):
    filter_complex = ""
    for i, (start, end) in enumerate(intervals):
        end = f":end={end}" if end is not None else ""
        filter_complex += f"[0:v]trim=start={start}{end},setpts=PTS-STARTPTS[v{i}];"  # Video trim for interval
        filter_complex += f"[0:a]atrim=start={start}{end},asetpts=PTS-STARTPTS[a{i}];"  # Audio trim for interval

    all_concat = "".join([f"[v{i}][a{i}]" for i in range(len(intervals))])
    filter_complex += f"{all_concat}concat=n={len(intervals)}:v=1:a=1[v][a]"

    cmd = f'ffmpeg -i {input_file} -filter_complex "{filter_complex}" -map "[v]" -map "[a]" {output_file}'
    return cmd


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python trim_silence.py silence.log input.mp4 output.mp4")
        sys.exit(1)

    log_file = sys.argv[1]
    input_file = sys.argv[2]
    output_file = sys.argv[3]

    intervals = parse_silence_log(log_file)
    if not intervals:
        sys.exit(1)

    cmd = generate_ffmpeg_trim_command(input_file, intervals, output_file)
    print("Generated FFmpeg command:")
    print(cmd)
