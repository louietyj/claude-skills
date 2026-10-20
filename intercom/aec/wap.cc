// C ABI over WebRTC's AudioProcessing, from ai-ecoverse/homescoop
// (packages/webrtc-audio-processing/src/wap.cc, Apache-2.0), plus wap_delay_config.
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <new>
#include <optional>

#include "api/audio/echo_canceller3_config.h"
#include "api/audio/echo_control.h"
#include "api/scoped_refptr.h"
#include "common_audio/vad/include/webrtc_vad.h"
#include "modules/audio_processing/aec3/echo_canceller3.h"
#include "modules/audio_processing/include/audio_processing.h"

namespace {

// The delay estimator is most of AEC3's cost in wasm: on the watch these settings
// halve it with unchanged echo and speech levels, and 5 filters still search
// ~0.5 s. wap_delay_config applies to cancellers created after it.
webrtc::EchoCanceller3Config DefaultConfig() {
  webrtc::EchoCanceller3Config c;
  c.delay.num_filters = 5;
  c.delay.down_sampling_factor = 8;
  c.delay.detect_pre_echo = false;
  return c;
}
webrtc::EchoCanceller3Config aec3_config = DefaultConfig();

class Aec3Factory : public webrtc::EchoControlFactory {
 public:
  explicit Aec3Factory(const webrtc::EchoCanceller3Config& config) : config_(config) {}
  std::unique_ptr<webrtc::EchoControl> Create(int rate, int render_channels, int capture_channels) override {
    return std::make_unique<webrtc::EchoCanceller3>(config_, std::nullopt, rate, render_channels, capture_channels);
  }

 private:
  webrtc::EchoCanceller3Config config_;
};

struct Wap {
  rtc::scoped_refptr<webrtc::AudioProcessing> apm;
  VadInst* vad = nullptr;
  int rate = 0;
  int frame = 0;
  int voice = -1;
  float* mic = nullptr;
  float* ref = nullptr;
  float* out = nullptr;
  int16_t* pcm = nullptr;
};

}

extern "C" {

__attribute__((export_name("wap_create"))) Wap* wap_create(int rate, int ns, int vad) {
  if (rate != 16000 && rate != 48000) return nullptr;
  Wap* w = new (std::nothrow) Wap();
  if (!w) return nullptr;
  w->rate = rate;
  w->frame = rate / 100;
  w->mic = static_cast<float*>(std::calloc(w->frame, sizeof(float)));
  w->ref = static_cast<float*>(std::calloc(w->frame, sizeof(float)));
  w->out = static_cast<float*>(std::calloc(w->frame, sizeof(float)));
  w->pcm = static_cast<int16_t*>(std::calloc(w->frame, sizeof(int16_t)));
  webrtc::AudioProcessing::Config config;
  config.echo_canceller.enabled = true;
  config.echo_canceller.mobile_mode = false;
  config.high_pass_filter.enabled = true;
  config.noise_suppression.enabled = ns > 0;
  if (ns == 1) config.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kLow;
  if (ns == 2) config.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kModerate;
  if (ns == 3) config.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kHigh;
  if (ns == 4) config.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kVeryHigh;
  w->apm = webrtc::AudioProcessingBuilder()
               .SetConfig(config)
               .SetEchoControlFactory(std::make_unique<Aec3Factory>(aec3_config))
               .Create();
  if (vad >= 0) {
    w->voice = 0;
    w->vad = WebRtcVad_Create();
    if (w->vad && (WebRtcVad_Init(w->vad) != 0 || WebRtcVad_set_mode(w->vad, vad > 3 ? 3 : vad) != 0)) {
      WebRtcVad_Free(w->vad);
      w->vad = nullptr;
    }
  }
  if (!w->apm || !w->mic || !w->ref || !w->out || !w->pcm || (vad >= 0 && !w->vad)) {
    if (w->vad) WebRtcVad_Free(w->vad);
    std::free(w->mic);
    std::free(w->ref);
    std::free(w->out);
    std::free(w->pcm);
    delete w;
    return nullptr;
  }
  return w;
}

__attribute__((export_name("wap_delay_config"))) void wap_delay_config(int num_filters, int down_sampling_factor,
                                                                       int detect_pre_echo) {
  aec3_config.delay.num_filters = num_filters;
  aec3_config.delay.down_sampling_factor = down_sampling_factor;
  aec3_config.delay.detect_pre_echo = detect_pre_echo != 0;
}

__attribute__((export_name("wap_frame"))) int wap_frame(Wap* w) { return w->frame; }
__attribute__((export_name("wap_mic"))) float* wap_mic(Wap* w) { return w->mic; }
__attribute__((export_name("wap_ref"))) float* wap_ref(Wap* w) { return w->ref; }
__attribute__((export_name("wap_out"))) float* wap_out(Wap* w) { return w->out; }

__attribute__((export_name("wap_process"))) int wap_process(Wap* w) {
  webrtc::StreamConfig sc(w->rate, 1);
  float* ref[1] = {w->ref};
  const float* mic[1] = {w->mic};
  float* out[1] = {w->out};
  int r = w->apm->ProcessReverseStream(ref, sc, sc, ref);
  if (r != 0) return r;
  r = w->apm->ProcessStream(mic, sc, sc, out);
  w->apm->GetStatistics();
  if (r != 0) return r;
  if (w->vad) {
    for (int i = 0; i < w->frame; i++) {
      float s = w->out[i] * 32768.0f;
      if (s > 32767.0f) s = 32767.0f;
      if (s < -32768.0f) s = -32768.0f;
      w->pcm[i] = static_cast<int16_t>(s);
    }
    w->voice = WebRtcVad_Process(w->vad, w->rate, w->pcm, w->frame);
  }
  return 0;
}

__attribute__((export_name("wap_voice"))) int wap_voice(Wap* w) { return w->voice; }

__attribute__((export_name("wap_stats"))) int wap_stats(Wap* w, double* out) {
  webrtc::AudioProcessingStats s = w->apm->GetStatistics();
  int have = 0;
  if (s.delay_ms) { out[0] = *s.delay_ms; have |= 1; }
  if (s.echo_return_loss_enhancement) { out[1] = *s.echo_return_loss_enhancement; have |= 2; }
  if (s.echo_return_loss) { out[2] = *s.echo_return_loss; have |= 4; }
  if (s.divergent_filter_fraction) { out[3] = *s.divergent_filter_fraction; have |= 8; }
  if (s.delay_median_ms) { out[4] = *s.delay_median_ms; have |= 16; }
  return have;
}

__attribute__((export_name("wap_destroy"))) void wap_destroy(Wap* w) {
  if (!w) return;
  if (w->vad) WebRtcVad_Free(w->vad);
  std::free(w->mic);
  std::free(w->ref);
  std::free(w->out);
  std::free(w->pcm);
  delete w;
}

__attribute__((export_name("wap_alloc"))) void* wap_alloc(int n) { return std::malloc(n); }
__attribute__((export_name("wap_free"))) void wap_free(void* p) { std::free(p); }

}
