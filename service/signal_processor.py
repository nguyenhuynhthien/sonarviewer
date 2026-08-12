import numpy as np
from constants import (
    VOLTAGE_SCALE_RX0, Q15_MAX_VAL_RX0, Q15_MAX_VAL_RX12,
    VOLTAGE_SCALE_RX_RAW_OFFSET, VOLTAGE_SCALE_RX_RAW_MULT,
    VOLTAGE_SCALE_RX_DEMOD_MULT, VOLTAGE_SCALE_RX_COMPRESSED_MULT,
    VOLTAGE_CLIP_RX_COMPRESSED, COMPRESSED_MAX_VAL,
    ACTIVE_SIGNAL_START_IDX, SMOOTHING_WINDOW_SIZE,
    CFAR_CUT_SIZE, CFAR_GUARD_SIZE_BARKER13, CFAR_GUARD_SIZE_SINGLE,
    BIAS_COMPRESSED, BIAS_RAW_DEMOD,
    FILTER_LEN_BARKER13, FILTER_LEN_SINGLE,
    DOWNSAMPLE_FACTOR
)

def convert_samples_to_voltages(samples, receiver_id, stream_idx):
    """Convert raw Q15/compressed samples to physical voltages based on receiver channel and stream mode."""
    if len(samples) == 0:
        return np.array([], dtype=np.float32)
        
    if receiver_id == 0:
        # STM32 ADCService sends unsigned 12-bit ADC samples, not Q15 data.
        return (np.clip(samples, 0.0, 4095.0) / 4095.0) * 3.3
    else:
        if stream_idx == 0:  # Raw
            return (samples / Q15_MAX_VAL_RX12) * VOLTAGE_SCALE_RX_RAW_MULT + VOLTAGE_SCALE_RX_RAW_OFFSET
        elif stream_idx == 2:  # Compressed
            return np.clip((samples / COMPRESSED_MAX_VAL) * VOLTAGE_SCALE_RX_COMPRESSED_MULT, 0.0, VOLTAGE_CLIP_RX_COMPRESSED)
        else:  # Demodulated
            return (samples / Q15_MAX_VAL_RX12) * VOLTAGE_SCALE_RX_DEMOD_MULT

def calculate_snr(voltages, pulse_type, tx_on, receiver_id, stream_idx):
    """Calculate the signal-to-noise ratio (SNR) in dB using a CFAR-like window approach."""
    if len(voltages) <= ACTIVE_SIGNAL_START_IDX or not tx_on:
        return None

    active_voltages = voltages[ACTIVE_SIGNAL_START_IDX:]
    baseline = np.median(active_voltages)
    deviation = np.abs(active_voltages - baseline)
    
    # Find peak index using a smoothed deviation
    smoothed_dev = np.convolve(deviation, np.ones(SMOOTHING_WINDOW_SIZE)/SMOOTHING_WINDOW_SIZE, mode='same')
    peak_idx_active = np.argmax(smoothed_dev)
    peak_idx = ACTIVE_SIGNAL_START_IDX + peak_idx_active
    
    # Define CFAR-like window parameters
    if pulse_type == 'barker13':
        cut_size = CFAR_CUT_SIZE
        guard_size = CFAR_GUARD_SIZE_BARKER13
    else:
        cut_size = CFAR_CUT_SIZE
        guard_size = CFAR_GUARD_SIZE_SINGLE

    # Define CUT (Signal) region
    cut_start = max(ACTIVE_SIGNAL_START_IDX, peak_idx - cut_size // 2)
    cut_end = min(len(voltages), peak_idx + cut_size // 2 + 1)
    signal_samples = voltages[cut_start:cut_end]
    
    # Define Guard region boundaries
    guard_start = max(ACTIVE_SIGNAL_START_IDX, peak_idx - cut_size // 2 - guard_size)
    guard_end = min(len(voltages), peak_idx + cut_size // 2 + guard_size + 1)
    
    # Reference Cells (Noise region)
    noise_samples = np.concatenate([voltages[ACTIVE_SIGNAL_START_IDX:guard_start], voltages[guard_end:]])
    if len(noise_samples) == 0:
        return None
        
    # True Radar Peak Signal Amplitude
    signal_peak = np.max(signal_samples) - baseline
    
    # Noise RMS using robust MAD
    noise_baseline = np.median(noise_samples)
    noise_deviation = np.abs(noise_samples - noise_baseline)
    mad = np.median(noise_deviation)
    
    noise_rms = mad / 0.6745 if mad > 1e-6 else np.std(noise_samples)
    
    if noise_rms > 1e-6 and signal_peak > 1e-6:
        raw_snr = 20 * np.log10(signal_peak / noise_rms)
        
        # Calibrate out the peak selection bias
        is_compressed = (receiver_id == 0) or (stream_idx == 2)
        bias = BIAS_COMPRESSED if is_compressed else BIAS_RAW_DEMOD
        
        calibrated_snr = raw_snr - bias
        return calibrated_snr
    return None

def shift_voltages(voltages, pulse_type):
    """Shift voltages to align radar history with the target distance (correcting for filter delay)."""
    filter_len = FILTER_LEN_BARKER13 if pulse_type == 'barker13' else FILTER_LEN_SINGLE
    if len(voltages) <= filter_len:
        return voltages
        
    shifted_voltages = np.zeros_like(voltages)
    shifted_voltages[:-filter_len] = voltages[filter_len:]
    shifted_voltages[-filter_len:] = np.median(voltages)
    return shifted_voltages

def process_radar_intensities(samples, pulse_type):
    """Process intensities for radar history display: deviation from baseline and downsampling."""
    if len(samples) == 0:
        return np.array([], dtype=np.float32)
        
    # Calculate intensities: absolute deviation from median (baseline)
    baseline = np.median(samples)
    deviation = np.abs(samples - baseline)
    
    # Downsample to DOWNSAMPLED_BINS for fast rendering
    downsampled = deviation.reshape(-1, DOWNSAMPLE_FACTOR).mean(axis=1)
    max_val = np.max(downsampled) if np.max(downsampled) > 0 else 1.0
    normalized = downsampled / max_val
    return normalized
