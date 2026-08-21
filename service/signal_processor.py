import numpy as np
from constants import (
    FS,
    VOLTAGE_SCALE_RX0, Q15_MAX_VAL_RX0, Q15_MAX_VAL_RX12,
    VOLTAGE_SCALE_RX_RAW_OFFSET, VOLTAGE_SCALE_RX_RAW_MULT,
    VOLTAGE_SCALE_RX_DEMOD_MULT, VOLTAGE_SCALE_RX_COMPRESSED_MULT,
    VOLTAGE_CLIP_RX_COMPRESSED, COMPRESSED_MAX_VAL,
    ACTIVE_SIGNAL_START_IDX, SMOOTHING_WINDOW_SIZE,
    CFAR_CUT_SIZE, CFAR_GUARD_SIZE_LFM, CFAR_GUARD_SIZE_BARKER13, CFAR_GUARD_SIZE_SINGLE,
    BIAS_COMPRESSED, BIAS_RAW_DEMOD,
    FILTER_LEN_LFM, FILTER_LEN_BARKER13, FILTER_LEN_SINGLE,
    DOWNSAMPLE_FACTOR
)

def convert_samples_to_voltages(samples, receiver_id, stream_idx):
    """Convert raw Q15/compressed samples to physical voltages based on receiver channel and stream mode."""
    if len(samples) == 0:
        return np.array([], dtype=np.float32)
        
    if receiver_id in (0, 3):
        # Rx Sum (0) and Rx Diff (3) are average norms scaled by 1/2048. Reconstruct mags using sqrt.
        mags = np.sqrt(np.clip(samples, 0.0, None) * 2048.0)
        return np.clip((mags / COMPRESSED_MAX_VAL) * VOLTAGE_SCALE_RX_COMPRESSED_MULT, 0.0, VOLTAGE_CLIP_RX_COMPRESSED)
    else:
        if stream_idx in (0, 1):  # Raw or BPF
            return (samples / 4096.0) * 3.3
        elif stream_idx == 3:  # Compressed
            # Reconstruct magnitude from single-pulse norm (shifted by 12 on kit)
            mags = np.sqrt(np.clip(samples, 0.0, None) * 4096.0)
            return np.clip((mags / COMPRESSED_MAX_VAL) * VOLTAGE_SCALE_RX_COMPRESSED_MULT, 0.0, VOLTAGE_CLIP_RX_COMPRESSED)
        else:  # Demodulated
            # Reconstruct magnitude from single-pulse norm (shifted by 12 on kit)
            mags = np.sqrt(np.clip(samples, 0.0, None) * 4096.0)
            return (mags / Q15_MAX_VAL_RX12) * VOLTAGE_SCALE_RX_DEMOD_MULT

def calculate_snr(voltages, pulse_type, tx_on, receiver_id, stream_idx):
    """Calculate the signal-to-noise ratio (SNR) in dB using a CFAR-like window approach."""
    if len(voltages) <= ACTIVE_SIGNAL_START_IDX:
        return None

    active_voltages = voltages[ACTIVE_SIGNAL_START_IDX:]
    baseline = np.median(active_voltages)
    deviation = np.abs(active_voltages - baseline)
    
    # Find peak index using a smoothed deviation
    smoothed_dev = np.convolve(deviation, np.ones(SMOOTHING_WINDOW_SIZE)/SMOOTHING_WINDOW_SIZE, mode='same')
    peak_idx_active = np.argmax(smoothed_dev)
    peak_idx = ACTIVE_SIGNAL_START_IDX + peak_idx_active
    
    # Define CFAR-like window parameters
    if pulse_type in ('lfm', 'barker13'):
        cut_size = CFAR_CUT_SIZE
        guard_size = CFAR_GUARD_SIZE_LFM
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
        is_compressed = (receiver_id in (0, 3)) or (stream_idx == 3)
        bias = BIAS_COMPRESSED if is_compressed else BIAS_RAW_DEMOD
        
        calibrated_snr = raw_snr - bias
        return calibrated_snr
    return None

def shift_voltages(voltages, pulse_type):
    """Shift voltages to align radar history with the target distance (correcting for filter delay)."""
    filter_len = FILTER_LEN_LFM if pulse_type in ('lfm', 'barker13') else FILTER_LEN_SINGLE
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

def compute_spectrum(voltages, fs=FS):
    """Compute single-sided raw FFT magnitude spectrum of the given voltage signal.
    Returns:
        freqs_khz (np.ndarray): Frequency array in kHz (from 0 to Fs/2 / 1000).
        magnitudes (np.ndarray): Raw FFT magnitude spectrum (independent of pulse type/compression ratio).
    """
    if len(voltages) == 0:
        return np.array([], dtype=np.float32), np.array([], dtype=np.float32)
        
    n = len(voltages)
    # Remove DC component / baseline to eliminate DC artifact
    baseline = np.median(voltages)
    ac_signal = voltages - baseline
    
    fft_result = np.fft.rfft(ac_signal)
    freqs_khz = np.fft.rfftfreq(n, d=1.0 / fs) / 1000.0  # in kHz
    
    # Raw FFT magnitude (objective, independent of pulse length and compression ratio)
    magnitudes = np.abs(fft_result)
    
    return freqs_khz, magnitudes
