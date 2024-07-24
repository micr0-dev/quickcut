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


def split_video(input_file, chunk_length):
    split_files = []
    cmd = (
        f"ffmpeg -i {shlex.quote(input_file)} -c copy -map 0 "
        f"-segment_time {chunk_length} -f segment -reset_timestamps 1 "
        f"temp_chunk_%03d.mkv"
    )
    subprocess.run(cmd, shell=True, check=True)

    for file in sorted(os.listdir()):
        if file.startswith("temp_chunk_") and file.endswith(".mkv"):
            split_files.append(file)

    if not split_files:
        print("Failed to split video.")
        return None

    return split_files


def generate_silence_log(input_file, log_file):
    input_file_quoted = shlex.quote(input_file)
    log_file_quoted = shlex.quote(log_file)
    cmd = f"ffmpeg -i {input_file_quoted} -af silencedetect=noise=-30dB:d=0.5 -f null - 2> {log_file_quoted}"

    total_duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {input_file_quoted}"
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


def generate_ffmpeg_trim_command(input_file, filter_file, output_file, use_nvenc):
    input_file_quoted = shlex.quote(input_file)
    filter_file_quoted = shlex.quote(filter_file)
    output_file_quoted = shlex.quote(output_file)
    nvenc_opts = "-c:v h264_nvenc" if use_nvenc else ""
    cmd = f"ffmpeg -i {input_file_quoted} -filter_complex_script {filter_file_quoted} {nvenc_opts} -map [v] -map [a] {output_file_quoted}"
    return cmd


def summarize_silence(input_file, intervals):
    input_file_quoted = shlex.quote(input_file)
    total_duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {input_file_quoted}"
    total_duration = float(
        subprocess.check_output(total_duration_cmd, shell=True).strip()
    )

    silent_duration = sum(end - start for start, end in intervals if end is not None)

    print(f"\nSummary:")
    print(f"Total video duration: {total_duration:.2f} seconds")
    print(f"Total silence detected: {silent_duration:.2f} seconds")
    print(
        f"Video duration after removing silence: {total_duration - silent_duration:.2f} seconds"
    )


def concatenate_videos(video_files, output_file):
    with open("concat_list.txt", "w") as f:
        for video in video_files:
            f.write(f"file '{os.path.abspath(video)}'\n")

    cmd = f"ffmpeg -f concat -safe 0 -i concat_list.txt -c copy {shlex.quote(output_file)}"
    subprocess.run(cmd, shell=True, check=True)
    os.remove("concat_list.txt")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trim_silence.py input.mp4")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = input_file.split(".")[-2] + "NoSilence." + input_file.split(".")[-1]
    chunk_length = int(sys.argv[2]) if len(sys.argv) > 3 else 600  # Default 10 minutes

    log_file = "silence.log"
    filter_file = "filter_complex.txt"

    try:
        # Check for NVENC availability
        use_nvenc = is_nvenc_available()
        print(f"Using NVENC: {use_nvenc}")

        # Split video into chunks
        split_files = split_video(input_file, chunk_length)
        if not split_files:
            sys.exit(1)

        processed_files = []

        for chunk in split_files:
            print(f"Processing chunk: {chunk}")

            # Generate silence log for the chunk
            generate_silence_log(chunk, log_file)

            # Parse the silence log to get intervals for the chunk
            intervals = parse_silence_log(log_file)
            if not intervals:
                # If no silence detected, add the chunk to processed_files as-is
                processed_files.append(chunk)
                continue

            # Summarize silence detection
            summarize_silence(chunk, intervals)

            # Save filter complex to a file
            save_filter_complex_to_file(intervals, filter_file)

            # Generate the FFmpeg command for the chunk
            output_chunk = f"processed_{chunk}"
            cmd = generate_ffmpeg_trim_command(
                chunk, filter_file, output_chunk, use_nvenc
            )
            print("Generated FFmpeg command:")
            print(cmd)

            # Execute the FFmpeg command for the chunk
            subprocess.run(cmd, shell=True, check=True)
            processed_files.append(output_chunk)

            # Clean up: remove the log files and filter complex file for the chunk
            if os.path.exists(log_file):
                os.remove(log_file)
            if os.path.exists(filter_file):
                os.remove(filter_file)

        # Concatenate processed chunks
        concatenate_videos(processed_files, output_file)

        # Print summary comparing original and processed video
        print("\n\nOriginal video:")
        total_duration = float(
            subprocess.check_output(
                f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(input_file)}",
                shell=True,
            ).strip()
        )
        print(f"Duration: {total_duration:.2f} seconds")

        print("\nProcessed video:")
        processed_duration = float(
            subprocess.check_output(
                f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(output_file)}",
                shell=True,
            ).strip()
        )
        print(f"Duration: {processed_duration:.2f} seconds")

        print(
            f"\nRemoved { total_duration - processed_duration:.2f} seconds of silence"
        )
        print(
            f"That's a {((total_duration - processed_duration) / total_duration) * 100:.2f}% reduction in video duration"
        )
        print(f"Processed video saved as: {output_file}")
    finally:
        # Clean up: remove temporary chunk files
        for file in split_files + processed_files:
            if os.path.exists(file) and "temp_chunk_" in file:
                os.remove(file)
