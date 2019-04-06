from __future__ import print_function

import sys
import wave

from io import StringIO

import pyaudio
import colorama
import numpy as np
import msvcrt
from reedsolo import RSCodec, ReedSolomonError
from termcolor import cprint
from pyfiglet import figlet_format

HANDSHAKE_START_HZ = 8192
HANDSHAKE_END_HZ = 8192 + 512

START_HZ = 1024
STEP_HZ = 256
BITS = 4

FEC_BYTES = 4

def stereo_to_mono(input_file, output_file):
    inp = wave.open(input_file, 'r')
    params = list(inp.getparams())
    params[0] = 1 # nchannels
    params[3] = 0 # nframes

    out = wave.open(output_file, 'w')
    out.setparams(tuple(params))

    frame_rate = inp.getframerate()
    frames = inp.readframes(inp.getnframes())
    data = np.fromstring(frames, dtype=np.int16)
    left = data[0::2]
    out.writeframes(left.tostring())

    inp.close()
    out.close()

def yield_chunks(input_file, interval):
    wav = wave.open(input_file)
    frame_rate = wav.getframerate()

    chunk_size = int(round(frame_rate * interval))
    total_size = wav.getnframes()

    while True:
        chunk = wav.readframes(chunk_size)
        if len(chunk) == 0:
            return

        yield frame_rate, np.fromstring(chunk, dtype=np.int16)

def dominant(frame_rate, chunk):
    w = np.fft.fft(chunk)
    freqs = np.fft.fftfreq(len(chunk))

    peak_coeff = np.argmax(np.abs(w))
    peak_freq = freqs[peak_coeff]
    return abs(peak_freq * frame_rate) # in Hz

def match(freq1, freq2):
    return abs(freq1 - freq2) < 20

def decode_bitchunks(chunk_bits, chunks):
    out_bytes = []

    next_read_chunk = 0
    next_read_bit = 0

    byte = 0
    bits_left = 8
    while next_read_chunk < len(chunks):
        can_fill = chunk_bits - next_read_bit
        to_fill = min(bits_left, can_fill)
        offset = chunk_bits - next_read_bit - to_fill

        byte <<= to_fill
        shifted = chunks[next_read_chunk] & (((1 << to_fill) - 1) << offset)
        byte |= shifted >> offset;
        bits_left -= to_fill
        next_read_bit += to_fill

        if bits_left <= 0:
            out_bytes.append(byte)
            byte = 0
            bits_left = 8

        if next_read_bit >= chunk_bits:
            next_read_chunk += 1
            next_read_bit -= chunk_bits

    return out_bytes

def decode_file(input_file, speed):
    wav = wave.open(input_file)
    if wav.getnchannels() == 2:
        mono = StringIO()
        stereo_to_mono(input_file, mono)

        mono.seek(0)
        input_file = mono
    wav.close()

    offset = 0
    for frame_rate, chunk in yield_chunks(input_file, speed / 2):
        dom = dominant(frame_rate, chunk)
        print("{} => {}".format(offset, dom))
        offset += 1

def extract_packet(freqs):
    freqs = freqs[::2]
    bit_chunks = [int(round((f - START_HZ) / STEP_HZ)) for f in freqs]
    bit_chunks = [c for c in bit_chunks if 0 <= c < (2 ** BITS)]
    return bytearray(decode_bitchunks(BITS, bit_chunks))

def display(s):
    s=s[12:-2]
    cprint(figlet_format(s, font='doom'), 'red')

def listen_linux(frame_rate=44100, interval=0.1):
    pa= pyaudio.PyAudio()
    dev_count=pa.get_device_count()
    for i in range(dev_count):
        if(pa.get_device_info_by_index(i)['maxInputChannels'] >  1):
            print(pa.get_device_info_by_index(i)['index'],pa.get_device_info_by_index(i)['name'])
    c1 = True
    while c1:
        k=input();
        if k:
            k=int(k)
            c1=False        
    num_frames = int(round((interval / 2) * frame_rate))
    mic = pa.open(format=pyaudio.paInt16,
                channels=1,
                rate=44100,
                input=True,
                frames_per_buffer=num_frames,
                input_device_index=k)


    in_packet = False
    packet = []
    run = True
    while run:
        data = mic.read(num_frames)
        chunk = np.frombuffer(data, dtype=np.int16)
        dom = dominant(frame_rate, chunk)
        if msvcrt.kbhit():
            run = False

        if in_packet and match(dom, HANDSHAKE_END_HZ):
            byte_stream = extract_packet(packet)

            try:
                byte_stream = RSCodec(FEC_BYTES).decode(byte_stream)
                display(str(byte_stream))
                display("")
            except ReedSolomonError as e:
                print("{}: {}".format(e, byte_stream))

            packet = []
            in_packet = False
        elif in_packet:
            packet.append(dom)
        elif match(dom, HANDSHAKE_START_HZ):
            in_packet = True

if __name__ == '__main__':

    colorama.init(strip=not sys.stdout.isatty())
    listen_linux()
