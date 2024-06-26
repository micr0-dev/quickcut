package main

import (
	"bufio"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/cheggaaa/pb/v3"
	ffmpeg "github.com/u2takey/ffmpeg-go"
)

func main() {
	// Check if verbose mode is enabled
	verbose := false
	for _, arg := range os.Args {
		if arg == "-v" || arg == "--verbose" {
			verbose = true
			break
		}
	}
	if verbose {
		// Suppress ffmpeg-go logging
		log.SetOutput(io.Discard)
	}

	if len(os.Args) < 2 {
		log.Fatal("Please provide an input file")
	}
	inputFile := os.Args[1]
	outputFile := strings.TrimSuffix(inputFile, filepath.Ext(inputFile)) + "_silenced" + filepath.Ext(inputFile)

	inputDuration := 0.0
	cmd := exec.Command("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", inputFile)
	stdout, err := cmd.Output()
	if err != nil {
		log.Fatalf("Failed to get duration of input file: %v", err)
	}

	fmt.Sscanf(string(stdout), "%f", &inputDuration)

	fmt.Println("Detecting silence in video...")
	cutPoints, err := detectSilence(inputFile, inputDuration)
	if err != nil {
		log.Fatalf("Failed to detect silence: %v", err)
	}

	fmt.Println("Cutting video...")
	err = cutVideo(inputFile, outputFile, cutPoints)
	if err != nil {
		log.Fatalf("Failed to cut video: %v", err)
	}

	cmd = exec.Command("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", outputFile)
	stdout, err = cmd.Output()
	if err != nil {
		log.Fatalf("Failed to get duration of output file: %v", err)
	}

	var outputDuration float64
	fmt.Sscanf(string(stdout), "%f", &outputDuration)
	totalDurationRemoved := inputDuration - outputDuration

	fmt.Printf("Silence removed successfully, output saved to %s\n", outputFile)
	fmt.Printf("Total duration removed: %.2f seconds\n", totalDurationRemoved)
}

func detectSilence(inputFile string, duration float64) ([][2]float64, error) {
	cmd := exec.Command(
		"ffmpeg", "-i", inputFile,
		"-af", "silencedetect=noise=-30dB:d=0.5",
		"-f", "null", "-",
	)

	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}

	if err := cmd.Start(); err != nil {
		return nil, err
	}

	cutPoints := make([][2]float64, 0)
	scanner := bufio.NewScanner(stderr)
	var start, end float64
	progressBar := pb.Full.Start(int(duration))

	for scanner.Scan() {
		line := scanner.Text()
		reStart := regexp.MustCompile(`silence_start: (?P<start>\d+\.\d+)`)
		reEnd := regexp.MustCompile(`silence_end: (?P<end>\d+\.\d+)`)

		if res := reStart.FindStringSubmatch(line); res != nil {
			fmt.Sscanf(res[1], "%f", &start)

			cutPoints = append(cutPoints, [2]float64{end, start})
		}
		if res := reEnd.FindStringSubmatch(line); res != nil {
			fmt.Sscanf(res[1], "%f", &end)
			progressBar.SetCurrent(int64(end))
		}
	}
	progressBar.Finish()
	cutPoints = append(cutPoints, [2]float64{end, 1e10}) // Add final segment

	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if err := cmd.Wait(); err != nil {
		return nil, err
	}

	// Remove any invalid large end time added as a last segment
	if cutPoints[len(cutPoints)-1][1] == 1e10 {
		cutPoints = cutPoints[:len(cutPoints)-1]
	}

	return cutPoints, nil
}

func cutVideo(inputFile, outputFile string, cutPoints [][2]float64) error {
	var segments []string
	progressBar := pb.Full.Start(len(cutPoints))

	for i, cut := range cutPoints {
		start := cut[0]
		end := cut[1]
		if start < end {
			segmentFile := fmt.Sprintf("seg_%d.mp4", i)
			segments = append(segments, segmentFile)
			err := ffmpeg.Input(inputFile, ffmpeg.KwArgs{"ss": fmt.Sprintf("%.3f", start), "to": fmt.Sprintf("%.3f", end)}).
				Output(segmentFile, ffmpeg.KwArgs{"c": "copy"}).
				OverWriteOutput().
				Run()
			if err != nil {
				return err
			}
		}
		progressBar.Increment()
	}
	progressBar.Finish()

	fileList := "filelist.txt"
	file, err := os.Create(fileList)
	if err != nil {
		return err
	}
	defer file.Close()
	writer := bufio.NewWriter(file)
	for _, segment := range segments {
		fmt.Fprintf(writer, "file '%s'\n", segment)
	}
	writer.Flush()

	err = ffmpeg.Input(fileList, ffmpeg.KwArgs{"f": "concat", "safe": "0"}).
		Output(outputFile, ffmpeg.KwArgs{"c": "copy"}).
		OverWriteOutput().
		Run()
	if err != nil {
		return err
	}

	// Cleanup temporary segment files
	for _, segment := range segments {
		os.Remove(segment)
	}
	os.Remove(fileList)

	return nil
}
