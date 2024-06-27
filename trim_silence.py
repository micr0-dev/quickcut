import os
import re
import sys
import subprocess
from tqdm import tqdm
import shlex


def is_nvenc_available():
    try:
        cmd = ["ffmpeg", "-encoders"]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return "h264_nvenc" in result.stdout
    except Exception as e:
        print(f"Error checking NVENC availability: {e}")
        return False


def generate_silence_log(input_file, log_file):
    cmd = f"ffmpeg -i {shlex.quote(input_file)} -af silencedetect=noise=-30dB:d=0.5 -f null - 2> {log_file}"

    total_duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(input_file)}"
    total_duration = float(
        subprocess.check_output(total_duration_cmd, shell=True).strip()
    )

    with tqdm(
        total=total_duration, desc="Generating silence log", unit="s", ncols=100
    ) as pbar:
        process = subprocess.Popen(
            cmd, shell=True, stderr=subprocess.PIPE, universal_newlines=True
        )
        for line in process.stderr:
            if "time=" in line:
                time = re.search(r"time=(\d+:\d+:\d+.\d+)", line)
                if time:
                    h, m, s = map(float, time.group(1).split(":"))
                    current_time = h * 3600 + m * 60 + s
                    pbar.n = current_time
                    pbar.refresh()
        process.wait()


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


def save_filter_complex_to_file(intervals, filter_file):
    filter_complex = ""
    for i, (start, end) in enumerate(intervals):
        end = f":end={end}" if end is not None else ""
        filter_complex += f"[0:v]trim=start={start}{end},setpts=PTS-STARTPTS[v{i}];"  # Video trim for interval
        filter_complex += f"[0:a]atrim=start={start}{end},asetpts=PTS-STARTPTS[a{i}];"  # Audio trim for interval

    all_concat = "".join([f"[v{i}][a{i}]" for i in range(len(intervals))])
    filter_complex += f"{all_concat}concat=n={len(intervals)}:v=1:a=1[v][a]"

    with open(filter_file, "w") as f:
        f.write(filter_complex)


def generate_ffmpeg_trim_command(input_file, filter_file, output_file):
    cmd = f"ffmpeg -i {shlex.quote(input_file)} -filter_complex_script {shlex.quote(filter_file)} -map [v] -map [a] {shlex.quote(output_file)}"
    return cmd


def summarize_silence(input_file, intervals):
    total_duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(input_file)}"
    total_duration = float(
        subprocess.check_output(total_duration_cmd, shell=True).strip()
    )

    silent_duration = sum(end - start for start, end in intervals if end is not None)

    print(f"\n#### Summary: ####")
    print(f"Total silence detected: {total_duration - silent_duration:.2f} seconds")
    print(f"Video duration after removing silence: {silent_duration:.2f} seconds")
    print(
        "Percentage of video to be removed: {:.2f}%".format(
            (1 - silent_duration / total_duration) * 100
        )
    )

    print("\nFFmpeg execution:\n\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trim_silence.py input.mp4")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = input_file.split(".")[-2] + "NoSilence." + input_file.split(".")[-1]
    log_file = "silence.log"
    filter_file = "filter_complex.txt"

    try:
        # Generate silence log using FFmpeg
        generate_silence_log(input_file, log_file)

        # Parse the silence log to get intervals
        intervals = parse_silence_log(log_file)
        if not intervals:
            sys.exit(1)

        # Summarize silence detection
        summarize_silence(input_file, intervals)

        # Save filter complex to a file
        save_filter_complex_to_file(intervals, filter_file)

        # Generate the FFmpeg command
        cmd = generate_ffmpeg_trim_command(input_file, filter_file, output_file)
        print("Generated FFmpeg command:")
        print(cmd)

        # Execute the FFmpeg command
        subprocess.run(cmd, shell=True, check=True)

    finally:
        # Clean up: remove the silence log file and filter complex file
        if os.path.exists(log_file):
            os.remove(log_file)
        if os.path.exists(filter_file):
            os.remove(filter_file)
