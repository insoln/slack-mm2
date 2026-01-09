package main

import (
	"bytes"
	"errors"
	"io"
	"testing"
)

// Test limitedReader with file exactly at limit
func TestLimitedReader_ExactLimit(t *testing.T) {
	data := []byte("12345")
	reader := newLimitedReader(bytes.NewReader(data), 5)
	
	result, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("Expected no error for file at limit, got: %v", err)
	}
	if !bytes.Equal(result, data) {
		t.Fatalf("Expected %v, got %v", data, result)
	}
}

// Test limitedReader with file under limit
func TestLimitedReader_UnderLimit(t *testing.T) {
	data := []byte("123")
	reader := newLimitedReader(bytes.NewReader(data), 5)
	
	result, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("Expected no error for file under limit, got: %v", err)
	}
	if !bytes.Equal(result, data) {
		t.Fatalf("Expected %v, got %v", data, result)
	}
}

// Test limitedReader with file over limit
func TestLimitedReader_OverLimit(t *testing.T) {
	data := []byte("123456789")
	reader := newLimitedReader(bytes.NewReader(data), 5)
	
	result, err := io.ReadAll(reader)
	if !errors.Is(err, errSizeExceeded) {
		t.Fatalf("Expected errSizeExceeded, got: %v", err)
	}
	// Should have read exactly 5 bytes before detecting overflow
	if len(result) != 5 {
		t.Fatalf("Expected to read 5 bytes, got %d", len(result))
	}
	if !bytes.Equal(result, []byte("12345")) {
		t.Fatalf("Expected first 5 bytes, got %v", result)
	}
}

// Test limitedReader with small buffer reads (simulates chunked reading)
func TestLimitedReader_SmallBuffers(t *testing.T) {
	data := []byte("1234567890")
	reader := newLimitedReader(bytes.NewReader(data), 5)
	
	// Read in 2-byte chunks
	var result []byte
	buf := make([]byte, 2)
	for {
		n, err := reader.Read(buf)
		if n > 0 {
			result = append(result, buf[:n]...)
		}
		if err != nil {
			if !errors.Is(err, errSizeExceeded) {
				t.Fatalf("Expected errSizeExceeded, got: %v", err)
			}
			break
		}
	}
	
	// Should have read exactly 5 bytes before error
	if len(result) != 5 {
		t.Fatalf("Expected to read 5 bytes, got %d", len(result))
	}
	if !bytes.Equal(result, []byte("12345")) {
		t.Fatalf("Expected first 5 bytes, got %v", result)
	}
}

// Test limitedReader with empty file
func TestLimitedReader_EmptyFile(t *testing.T) {
	data := []byte{}
	reader := newLimitedReader(bytes.NewReader(data), 5)
	
	result, err := io.ReadAll(reader)
	if err != nil && err != io.EOF {
		t.Fatalf("Expected EOF or no error for empty file, got: %v", err)
	}
	if len(result) != 0 {
		t.Fatalf("Expected empty result, got %v", result)
	}
}

// Test limitedReader with single byte reads
func TestLimitedReader_SingleByteReads(t *testing.T) {
	data := []byte("123456")
	reader := newLimitedReader(bytes.NewReader(data), 3)
	
	var result []byte
	buf := make([]byte, 1)
	for {
		n, err := reader.Read(buf)
		if n > 0 {
			result = append(result, buf[:n]...)
		}
		if err != nil {
			if !errors.Is(err, errSizeExceeded) {
				t.Fatalf("Expected errSizeExceeded, got: %v", err)
			}
			break
		}
	}
	
	// Should have read exactly 3 bytes before error
	if len(result) != 3 {
		t.Fatalf("Expected to read 3 bytes, got %d", len(result))
	}
	if !bytes.Equal(result, []byte("123")) {
		t.Fatalf("Expected first 3 bytes, got %v", result)
	}
}
