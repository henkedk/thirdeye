BINARY = thirdeye-injector
VERSION = 1.0.0

.PHONY: all build arm64 amd64 clean

all: arm64 amd64

build:
	go build -ldflags="-s -w" -o $(BINARY) .

arm64:
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o $(BINARY)-arm64 .

amd64:
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o $(BINARY)-amd64 .

clean:
	rm -f $(BINARY) $(BINARY)-arm64 $(BINARY)-amd64
