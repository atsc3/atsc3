#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#include <SoapySDR/Constants.h>
#include <SoapySDR/Device.hpp>
#include <SoapySDR/Errors.h>
#include <SoapySDR/Formats.hpp>
#include <SoapySDR/Registry.hpp>
#include <SoapySDR/Version.hpp>

#include "rsp.h"
#include "rsp_internal.h"

class SoapyRspOpen : public SoapySDR::Device {
public:
    explicit SoapyRspOpen(const SoapySDR::Kwargs &args) {
        (void)args;
        char err[256];
        dev = rsp_open(0, err, sizeof(err));
        if (!dev)
            throw std::runtime_error(std::string("rsp-open: ") + err);
        freq = 587e6;
        rate = 10e6;
        bw = 8e6;
        ifgr = 44;
        rfgr = 4;
        running = false;
        stream = nullptr;
    }

    ~SoapyRspOpen(void) override {
        if (dev)
            rsp_close(dev);
    }

    std::string getDriverKey(void) const override { return "rsp-open"; }
    std::string getHardwareKey(void) const override { return "RSP1B"; }

    SoapySDR::Kwargs getHardwareInfo(void) const override {
        SoapySDR::Kwargs info;
        info["device"] = "RSP1B";
        info["driver"] = "rsp-open";
        return info;
    }

    size_t getNumChannels(const int direction) const override {
        return direction == SOAPY_SDR_RX ? 1 : 0;
    }

    std::vector<std::string> listAntennas(const int direction,
                                          const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {"RX"};
    }

    void setAntenna(const int direction, const size_t channel,
                    const std::string &name) override {
        (void)direction;
        (void)channel;
        (void)name;
    }

    std::string getAntenna(const int direction,
                           const size_t channel) const override {
        (void)direction;
        (void)channel;
        return "RX";
    }

    std::vector<std::string> listGains(const int direction,
                                       const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {"IFGR", "RFGR"};
    }

    bool hasGainMode(const int direction, const size_t channel) const override {
        (void)direction;
        (void)channel;
        return false;
    }

    SoapySDR::Range getGainRange(const int direction, const size_t channel,
                                 const std::string &name) const override {
        (void)direction;
        (void)channel;
        if (name == "RFGR")
            return SoapySDR::Range(0, 9);
        return SoapySDR::Range(20, 59);
    }

    void setGain(const int direction, const size_t channel,
                 const std::string &name, const double value) override {
        (void)direction;
        (void)channel;
        if (running)
            throw std::runtime_error("rsp-open: set gain before activateStream");
        if (name == "RFGR")
            rfgr = (int)(value + 0.5);
        else
            ifgr = (int)(value + 0.5);
    }

    void setGain(const int direction, const size_t channel,
                 const double value) override {
        setGain(direction, channel, "IFGR", value);
    }

    double getGain(const int direction, const size_t channel,
                   const std::string &name) const override {
        (void)direction;
        (void)channel;
        return name == "RFGR" ? rfgr : ifgr;
    }

    double getGain(const int direction, const size_t channel) const override {
        return getGain(direction, channel, "IFGR");
    }

    void setFrequency(const int direction, const size_t channel,
                      const std::string &name, const double frequency,
                      const SoapySDR::Kwargs &args) override {
        (void)direction;
        (void)channel;
        (void)args;
        if (name != "RF")
            return;
        if (running)
            throw std::runtime_error("rsp-open: set frequency before activateStream");
        freq = frequency;
    }

    void setFrequency(const int direction, const size_t channel,
                      const double frequency,
                      const SoapySDR::Kwargs &args) override {
        setFrequency(direction, channel, "RF", frequency, args);
    }

    double getFrequency(const int direction, const size_t channel,
                        const std::string &name) const override {
        (void)direction;
        (void)channel;
        (void)name;
        return freq;
    }

    double getFrequency(const int direction,
                        const size_t channel) const override {
        (void)direction;
        (void)channel;
        return freq;
    }

    std::vector<std::string> listFrequencies(const int direction,
                                             const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {"RF"};
    }

    SoapySDR::RangeList getFrequencyRange(const int direction,
                                          const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {SoapySDR::Range(42e6, 1000e6)};
    }

    SoapySDR::RangeList getFrequencyRange(const int direction,
                                          const size_t channel,
                                          const std::string &name) const override {
        (void)name;
        return getFrequencyRange(direction, channel);
    }

    void setSampleRate(const int direction, const size_t channel,
                       const double sampleRate) override {
        (void)direction;
        (void)channel;
        if (running)
            throw std::runtime_error("rsp-open: set rate before activateStream");
        if (sampleRate < 9.9e6 || sampleRate > 10.1e6)
            throw std::runtime_error("rsp-open: only 10 MS/s is air-proven");
        rate = sampleRate;
    }

    double getSampleRate(const int direction,
                         const size_t channel) const override {
        (void)direction;
        (void)channel;
        return rate;
    }

    std::vector<double> listSampleRates(const int direction,
                                        const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {10e6};
    }

    SoapySDR::RangeList getSampleRateRange(const int direction,
                                           const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {SoapySDR::Range(10e6, 10e6)};
    }

    void setBandwidth(const int direction, const size_t channel,
                      const double bandwidth) override {
        (void)direction;
        (void)channel;
        if (running)
            throw std::runtime_error("rsp-open: set bandwidth before activateStream");
        if (bandwidth > 0 && (bandwidth < 7.9e6 || bandwidth > 8.1e6))
            throw std::runtime_error("rsp-open: only 8 MHz is air-proven");
        bw = 8e6;
    }

    double getBandwidth(const int direction,
                        const size_t channel) const override {
        (void)direction;
        (void)channel;
        return bw;
    }

    std::vector<double> listBandwidths(const int direction,
                                       const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {8e6};
    }

    SoapySDR::RangeList getBandwidthRange(const int direction,
                                          const size_t channel) const override {
        (void)direction;
        (void)channel;
        return {SoapySDR::Range(8e6, 8e6)};
    }

    SoapySDR::Stream *setupStream(const int direction,
                                  const std::string &format,
                                  const std::vector<size_t> &channels,
                                  const SoapySDR::Kwargs &args) override {
        (void)channels;
        (void)args;
        if (direction != SOAPY_SDR_RX)
            throw std::runtime_error("rsp-open: RX only");
        if (format != SOAPY_SDR_CS16)
            throw std::runtime_error("rsp-open: only CS16 is supported");
        stream = (SoapySDR::Stream *)this;
        return stream;
    }

    void closeStream(SoapySDR::Stream *handle) override {
        (void)handle;
        if (running)
            deactivateStream(stream, 0, 0);
        stream = nullptr;
    }

    size_t getStreamMTU(SoapySDR::Stream *handle) const override {
        (void)handle;
        return RSP_READ_CHUNK;
    }

    int activateStream(SoapySDR::Stream *handle, const int flags,
                       const long long timeNs, const size_t numElems) override {
        (void)handle;
        (void)flags;
        (void)timeNs;
        (void)numElems;
        if (running)
            return 0;
        rsp_config cfg;
        cfg.freq_hz = freq;
        cfg.rate_hz = rate;
        cfg.bandwidth_hz = bw;
        cfg.ifgr = ifgr;
        cfg.rfgr = rfgr;
        char err[256];
        if (rsp_configure(dev, &cfg, err, sizeof(err)) < 0 ||
            rsp_start(dev, err, sizeof(err)) < 0)
            throw std::runtime_error(std::string("rsp-open: ") + err);
        running = true;
        return 0;
    }

    int deactivateStream(SoapySDR::Stream *handle, const int flags,
                         const long long timeNs) override {
        (void)handle;
        (void)flags;
        (void)timeNs;
        if (running) {
            rsp_stop(dev);
            running = false;
        }
        return 0;
    }

    int readStream(SoapySDR::Stream *handle, void *const *buffs,
                   const size_t numElems, int &flags, long long &timeNs,
                   const long timeoutUs) override {
        (void)handle;
        int timeout_ms = (int)(timeoutUs / 1000);
        if (timeout_ms < 1)
            timeout_ms = 1;
        int16_t *dst = (int16_t *)buffs[0];
        size_t got = 0;
        while (got < numElems) {
            size_t chunk = std::min(numElems - got, (size_t)RSP_READ_CHUNK);
            int n = rsp_read(dev, dst + got * 2, chunk, timeout_ms);
            if (n < 0) {
                flags = 0;
                timeNs = 0;
                return SOAPY_SDR_STREAM_ERROR;
            }
            if (n == 0)
                break;
            got += (size_t)n;
        }
        flags = 0;
        timeNs = 0;
        if (got == 0)
            return SOAPY_SDR_TIMEOUT;
        return (int)got;
    }

private:
    rsp_dev *dev;
    SoapySDR::Stream *stream;
    double freq, rate, bw;
    int ifgr, rfgr;
    bool running;
};

static SoapySDR::KwargsList findRspOpen(const SoapySDR::Kwargs &args) {
    SoapySDR::KwargsList results;
    char serial[128];
    for (int i = 0; rsp_probe(i, serial, sizeof(serial)); i++) {
        if (args.count("serial") && args.at("serial") != serial)
            continue;
        SoapySDR::Kwargs info;
        info["driver"] = "rsp-open";
        info["label"] = "RSP1B";
        if (serial[0])
            info["serial"] = serial;
        results.push_back(info);
    }
    return results;
}

static SoapySDR::Device *makeRspOpen(const SoapySDR::Kwargs &args) {
    return new SoapyRspOpen(args);
}

static SoapySDR::Registry registryRspOpen("rsp-open", &findRspOpen,
                                          &makeRspOpen, SOAPY_SDR_ABI_VERSION);
