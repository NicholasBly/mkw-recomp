// Between perf.c (frame timing, always built) and the OS's sampling profiler (prof_win.c on Windows).
#pragma once
#include <stdint.h>

extern volatile uint32_t prof_render_tid;   // the thread that presents (perf_present)
extern volatile uint32_t prof_main_tid;     // the guest main thread (perf_main_thread)
extern volatile uint32_t prof_replay_tid;   // the asynchronous replay (perf_replay_thread): its waits hold up the frame too

// The sampler; perf.c's weak versions do nothing.
int prof_sampler_start(void);               // MKW_PROFILE: starts sampling; 1 when it runs
void prof_sampler_write(void);              // writes the profile once (prof_write)
void prof_sampler_frame(uint64_t frame, uint64_t now, uint64_t d);   // every present; d: the interval, 0 on the first

// MKW_FRAMES (frames.c): one row per present, from perf_present; flushed at exit (perf_summary_flush).
void frames_row(uint64_t frame, uint64_t interval_ns, const uint64_t wait_ns[], uint64_t replay_ns);
void frames_flush(void);
