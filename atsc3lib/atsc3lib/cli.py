"""Command-line interface for the ATSC 3.0 receiver.

``atsc3-decode`` runs the full validated signalling chain on a capture:

    capture -> bootstrap -> Preamble -> L1-Basic -> L1-Detail -> per-PLP config

``atsc3-capture`` (see :mod:`atsc3lib.capture`) records IQ from an SDR.
"""

import argparse
import logging
import os
import sys

from . import spec
from .receiver import decode_capture, decode_plp_payload, decode_cti_plp_streams
from .payload import TI_CTI

logger = logging.getLogger(__name__)

#: Offline default: a short, bounded drain.  One RF33 frame is 247.1 ms and a
#: broadcast media object spans several frames; four frames is the shortest
#: drain that yields a playable ROUTE/MMTP segment on the saved RF33 capture
#: (two is empty).  ``--frames 0`` drains the whole capture.
DEFAULT_MEDIA_FRAMES = 4


def _print_result(result):
    if result.l1_basic is not None:
        lb = result.l1_basic
        print(f"  L1-Basic: version {lb.version}, CRC {'OK' if lb.crc_ok else 'FAIL'}")
        print(f"    subframes           : {lb.num_subframes + 1}")
        print(f"    preamble symbols    : {lb.preamble_num_symbols + 1}")
        print(f"    L1-Detail fec type  : {lb.l1_detail_fec_type} (mode {lb.l1_detail_fec_type + 1})")
        print(f"    L1-Detail size      : {lb.l1_detail_size_bytes} bytes")
        print(f"    L1-Detail total cells: {lb.l1_detail_total_cells}")
    if result.l1_detail is not None:
        ld = result.l1_detail
        print(f"  L1-Detail: version {ld.version}, BSID {ld.bsid}, "
              f"CRC {'OK' if ld.crc_ok else 'FAIL'}")
        fft = {0: '8K', 1: '16K', 2: '32K'}
        print(f"  Per-PLP configuration ({len(result.plps)} PLP(s)):")
        for sf_idx, plp in result.plps:
            print(f"    subframe {sf_idx}  PLP {plp.plp_id:<3d} "
                  f"layer={plp.layer} start={plp.start} size={plp.size} "
                  f"fec={plp.fec_type} mod={plp.modulation} cod={plp.code_rate} "
                  f"TI={plp.ti_mode}")


def decode_main(argv=None):
    """CLI for the full signalling decode chain."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(
        description="ATSC 3.0 receiver: decode L1 signalling and PLP config")
    parser.add_argument('file', help='IQ capture file path')
    parser.add_argument('--rate', type=float, required=True,
                        help='capture sample rate in Hz (e.g. 10e6)')
    parser.add_argument('--fmt', default='auto',
                        choices=['auto', 'cs8', 'cs16', 'cf32'],
                        help="sample format (default: cs8, int8 interleaved IQ)")
    parser.add_argument('--max-iterations', type=int, default=100,
                        help='LDPC iteration cap')
    parser.add_argument('--plp', type=int, default=None,
                        help='also decode this PLP id (default: smallest layer-0 '
                             'PLP of --subframe)')
    parser.add_argument('--subframe', type=int, default=0,
                        help='subframe whose PLP to decode (default 0)')
    parser.add_argument('--no-payload', action='store_true',
                        help='decode signalling only, skip the PLP payload')
    parser.add_argument('--frames', type=int, default=6,
                        help='frames to gather for a CTI-mode (TI mode 1) PLP')
    parser.add_argument('--no-fine-timing', action='store_true',
                        help='do not refine the FFT window off the scattered '
                             'pilots (A/322 8.1.3.1)')
    parser.add_argument('--no-cpe', action='store_true',
                        help='do not run the decision-directed per-symbol '
                             'common-phase correction')
    parser.add_argument('--trust-root', action='append', default=[],
                        metavar='PEM',
                        help='verify signed LLS against this root certificate '
                             '(repeatable; enables the security gate)')
    parser.add_argument('--security-provider', default=None,
                        help='security verification backend (default: active, '
                             'usually openatsc3); see atsc3lib.security.available')
    parser.add_argument('--require-signature', action='store_true',
                        help='exit non-zero unless signed signaling verifies')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    result = decode_capture(args.file, args.rate, fmt=args.fmt,
                            max_iterations=args.max_iterations)

    print(f"Capture: {args.file} @ {args.rate/1e6:.3f} MHz")
    _print_result(result)
    if result.l1_basic is None:
        print(f"  FAILED: {result.error}")
        return 1
    if result.l1_detail is None:
        print(f"  L1-Detail FAILED: {result.error}")
        return 1

    if not args.no_payload:
        from .receiver import _read_iq, _guess_sample_format
        from .payload import decode_streams
        fmt = args.fmt if args.fmt != 'auto' else _guess_sample_format(args.file)
        iq = _read_iq(args.file, fmt)
        sf = result.l1_detail.subframes[args.subframe]
        cti = any(p.ti_mode == TI_CTI for p in sf['plps'] if p.layer == 0)
        if cti:
            result, decoded, streams = decode_cti_plp_streams(
                iq, args.rate, plp_id=args.plp, n_frames=args.frames,
                max_iterations=args.max_iterations, result=result,
                fine_timing=not args.no_fine_timing,
                cpe=not args.no_cpe)
            if decoded is None:
                print(f"  Payload: no CTI PLP decoded in subframe {args.subframe}")
                return 0
            print(f"  Payload: PLP {decoded.payload.plp_id}, CTI Nrows "
                  f"{decoded.nrows}, C {decoded.c_offset}, "
                  f"{decoded.payload.n_converged}/{decoded.n_blocks} FEC "
                  f"blocks converged")
        else:
            _, payload = decode_plp_payload(
                iq, args.rate, plp_id=args.plp, subframe=args.subframe,
                max_iterations=args.max_iterations, result=result)
            if payload is None:
                print(f"  Payload: no PLP decoded in subframe {args.subframe}")
                return 0
            print(f"  Payload: PLP {payload.plp_id}, "
                  f"{payload.n_converged}/{payload.n_fec} FEC blocks converged")
            streams = decode_streams(payload)
        print(f"  Streams: {len(streams.packets)} ALP packet(s), "
              f"{len(streams.datagrams)} UDP datagram(s), "
              f"{len(streams.lls)} LLS table(s)")
        for t in streams.lls:
            print(f"    LLS table 0x{t.table_id:02x} ({t.name}): "
                  f"{len(t.data)} bytes")
        if streams.alp_stats.resync:
            print(f"    (ALP resyncs: {streams.alp_stats.resync})")
        if args.trust_root:
            from . import security
            try:
                roots = security.load_trust_roots(args.trust_root,
                                                  args.security_provider)
                report = security.verify_streams(streams, roots,
                                                 provider=args.security_provider)
            except Exception as exc:
                print(f"  Security: unavailable ({exc})")
                return 2 if args.require_signature else 0
            print(f"  Security [{report.provider}]: {security.describe(report)}")
            if args.require_signature and not report.ok:
                return 2
    return 0


def media_main(argv=None):
    """CLI: an IQ capture to playable fragmented-MP4 media files.

    Acquires once, drains every frame, reassembles MMTP/ROUTE, and writes one
    ``.mp4`` per recovered track.  Video is the default; ``--all`` writes
    audio/subtitle too.  The output is a fragmented MP4 a player opens once the
    fragments are retimed onto one timeline (see :mod:`atsc3lib.mp4`).
    """
    parser = argparse.ArgumentParser(
        description="ATSC 3.0 capture -> playable fragmented MP4")
    parser.add_argument('file', help='IQ capture file path')
    parser.add_argument('--rate', type=float, required=True,
                        help='capture sample rate in Hz (e.g. 10e6)')
    parser.add_argument('--fmt', default='auto',
                        choices=['auto', 'cs8', 'cs16', 'cf32'])
    parser.add_argument('--plp', type=int, default=None,
                        help='PLP id to drain (default: every layer-0 PLP)')
    parser.add_argument('--subframe', type=int, default=0,
                        help='subframe for an explicit --plp (default 0)')
    parser.add_argument('--frames', type=int, default=DEFAULT_MEDIA_FRAMES,
                        help='max frames to decode per PLP '
                             f'(default {DEFAULT_MEDIA_FRAMES}; 0 = whole capture)')
    parser.add_argument('--max-iterations', type=int, default=100)
    parser.add_argument('--full-search', action='store_true',
                        help='search the whole capture for the bootstrap')
    parser.add_argument('--no-repair', action='store_true',
                        help='drop truncated segments instead of trimming them')
    parser.add_argument('--all', action='store_true',
                        help='write every track, not just the video')
    parser.add_argument('--no-mux', action='store_true',
                        help='do not write the combined A/V MP4 '
                             '(per-track files and WAVs are still written)')
    parser.add_argument('--audio-track', type=int, default=None,
                        help='AC-4 track id to mux into the A/V file '
                             '(default: the longest)')
    parser.add_argument('-o', '--outdir', default='out/media',
                        help='output directory (default: out/media)')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    from .receiver import _guess_sample_format, _read_iq
    from . import mp4

    fmt = args.fmt if args.fmt != 'auto' else _guess_sample_format(args.file)
    iq = _read_iq(args.file, fmt)
    max_frames = args.frames if args.frames else None
    result, media = mp4.build_from_iq(
        iq, args.rate, plp_id=args.plp, subframe=args.subframe,
        max_iterations=args.max_iterations, full_search=args.full_search,
        max_frames=max_frames, repair=not args.no_repair)
    if result is None or not result.l1_detail_ok:
        print(f"  Acquire FAILED: {result.error if result else 'no result'}")
        return 1
    if media is None:
        print("  No media reassembled")
        return 0

    tracks = mp4.build_tracks(media, repair=not args.no_repair)
    if not tracks:
        print("  No playable track recovered")
        return 0
    if not args.all:
        video = mp4.select_track(tracks, b"vide")
        tracks = [video] if video is not None else tracks[:1]

    os.makedirs(args.outdir, exist_ok=True)
    for built in tracks:
        flow = built.dst_ip.replace('.', '_')
        name = (f"{flow}_{built.dst_port}_{built.transport}_"
                f"{built.track_id}_{built.handler.decode('latin1')}.mp4")
        path = os.path.join(args.outdir, name)
        with open(path, 'wb') as fh:
            fh.write(built.data)
        truncated = sum(1 for _s, _n, t in built.segments if t)
        print(f"  {name}: {built.samples} sample(s), "
              f"{built.seconds:.2f} s, {len(built.data)} bytes, "
              f"{len(built.segments)} segment(s), "
              f"{truncated} truncated")
    decoded_audio = _write_audio(args, media)
    if not args.no_mux:
        _write_av(args, tracks, decoded_audio)
    return 0


def _write_audio(args, media):
    """Decode every AC-4 media track and write a WAV next to the MP4s.

    The A-SPX decoder lives in ``ac4bindings``; when that package is absent the
    whole step is a silent no-op.  Audio comes from the *reassembled* media
    (the raw frames), not the retimed MP4, which no longer carries the samples.
    Returns the decoded tracks for :func:`_write_av`.
    """
    try:
        from . import audio
    except Exception:
        return []
    decoded = audio.decode_media(media)
    for d in decoded:
        flow = d.dst_ip.replace('.', '_')
        name = (f"{flow}_{d.dst_port}_{d.transport}_"
                f"{d.track_id}_soun.wav")
        wav = os.path.join(args.outdir, name)
        audio.write_wav(wav, d)
        print(f"  {name}: {d.frame_count} sample(s), "
              f"{d.frame_count / d.sample_rate:.2f} s, "
              f"{'x'.join(d.channels)}")
    return decoded


def _write_av(args, tracks, decoded_audio):
    """Mux the longest video track and the decoded AC-4 audio into one file.

    This is the artifact an operator opens once to both watch and listen; it
    stream-copies the HEVC video and encodes the AC-4 PCM to AAC.  Silent when
    PyAV is absent or no video/audio pair exists.
    """
    try:
        from . import mux
    except Exception:
        return
    video = next((t for t in tracks if t.handler == b"vide"), None)
    if video is None or not decoded_audio:
        return
    chosen = mux.select_audio(decoded_audio, args.audio_track)
    if chosen is None:
        return
    flow = video.dst_ip.replace('.', '_')
    name = (f"{flow}_{video.dst_port}_{video.transport}_"
            f"{video.track_id}+{chosen.track_id}_av.mp4")
    path = os.path.join(args.outdir, name)
    try:
        mux.mux_av(video, decoded_audio, path,
                   audio_track_id=chosen.track_id)
    except Exception as exc:
        print(f"  A/V mux skipped: {exc}")
        return
    print(f"  {name}: video track {video.track_id} + AC-4 track "
          f"{chosen.track_id} ({'x'.join(chosen.channels)})")


def live_main(argv=None):
    """CLI for the bounded live receive loop.

    Runs a finite number of acquisition windows (``--units``) against either a
    live SDRplay or a saved capture, decoding each locked frame's PLP payload
    and reporting the streams seen.
    """
    import argparse

    from .live import (
        FileIqSource, LiveConfig, LiveMediaSink, LiveReceiver,
        SdrplayIqSource)

    parser = argparse.ArgumentParser(
        description="ATSC 3.0 bounded live receive loop")
    parser.add_argument('--file', default=None,
                        help='saved IQ capture to replay (else live SDRplay)')
    parser.add_argument('--freq', type=float, default=587e6,
                        help='center frequency in Hz (live mode)')
    parser.add_argument('--rate', type=float, default=10e6,
                        help='sample rate in Hz')
    parser.add_argument('--fmt', default='cs16', choices=['cs8', 'cs16', 'cf32'],
                        help='file sample format')
    parser.add_argument('--plp', type=int, default=None,
                        help='PLP id to decode (default: smallest layer-0 PLP)')
    parser.add_argument('--subframe', type=int, default=0)
    parser.add_argument('--units', type=int, default=None,
                        help='max acquisition windows per call (default: finite)')
    parser.add_argument('--max-frames', type=int, default=16,
                        help='max frames decoded per lock')
    parser.add_argument('--ifgr', type=float, default=40)
    parser.add_argument('--rfgr', type=float, default=4)
    parser.add_argument('--outdir', default=None,
                        help='write reassembled media/audio/A-V files here '
                             '(default: do not write media)')

    args = parser.parse_args(argv)

    if args.file:
        source = FileIqSource(args.file, args.rate, args.fmt)
    else:
        source = SdrplayIqSource(args.freq, sample_rate=args.rate,
                                 ifgr=args.ifgr, rfgr=args.rfgr)
    config = LiveConfig(plp_id=args.plp, subframe=args.subframe,
                        max_frames=args.max_frames)
    receiver = LiveReceiver(source, config)
    seen = {'slt': 0, 'route': 0, 'mmtp': 0, 'datagrams': 0}

    def on_streams(streams):
        seen['datagrams'] += len(streams.datagrams)
        if getattr(streams, 'slt', None) is not None:
            seen['slt'] += 1
        for d in streams.datagrams:
            if d.dst_port == 4937:
                continue
            if d.payload[:1] and d.payload[0] >> 4 == 1:
                seen['route'] += 1
            elif d.payload[:1] and d.payload[0] >> 6 == 1:
                seen['mmtp'] += 1

    sink = None

    def observe(streams):
        on_streams(streams)
        if sink is not None:
            sink(streams)

    receiver.on_streams = observe
    if args.outdir:
        sink = LiveMediaSink(args.outdir)
    receiver.run(max_windows=args.units)
    print(f"windows={receiver.stats.windows} "
          f"acquisitions={receiver.stats.acquisitions} "
          f"frames={receiver.stats.frames_decoded} "
          f"payloads={receiver.stats.payloads} "
          f"datagrams={seen['datagrams']} "
          f"slt={seen['slt']} route={seen['route']} mmtp={seen['mmtp']}")
    if sink is not None:
        written = sink.build()
        print(f"  media files: {len(written)} "
              f"(dropped {sink.dropped} datagram(s))")
        for path in written:
            print(f"    {os.path.basename(path)}")
    source.close()
    return 0 if receiver.stats.acquisitions else 1


def transmit_main(argv=None):
    """CLI: build a synthetic ATSC 3.0 frame and write it as IQ.

    The transmitter is the inverse of the receive chain (see
    :mod:`atsc3lib.transmit`).  By default it emits one 8K/GI1536/SP4_2
    QPSK 2/15 frame carrying a minimal A/331 SLT, which ``atsc3-decode`` reads
    back.  ``--lls TABLE_ID:FILE`` adds raw LLS table payloads (with a
    generated SLT unless ``--no-slt``); ``--signed`` builds the own-CA
    CertificationData + SignedMultiTable with ``openatsc3_pki`` and signs the
    SLT (the certificate hook).
    """
    import numpy as np

    from . import transmit

    parser = argparse.ArgumentParser(
        description="ATSC 3.0 synthetic transmitter: build a frame, write IQ")
    parser.add_argument('-o', '--out', required=True,
                        help='output IQ file path')
    parser.add_argument('--fmt', default='cs8', choices=['cs8', 'cs16', 'cf32'],
                        help='output sample format (default: cs8)')
    parser.add_argument('--bsid', type=int, default=transmit.DEFAULT_BSID,
                        help=f'bsid to signal (default {transmit.DEFAULT_BSID})')
    parser.add_argument('--plp', type=int, default=16,
                        help='PLP id to signal and carry the payload in')
    parser.add_argument('--lls', action='append', default=[], metavar='ID:FILE',
                        help='add an LLS table: table_id (hex/dec) prefix and '
                             'a file holding the table payload (repeatable)')
    parser.add_argument('--no-slt', action='store_true',
                        help='do not add the default standalone SLT (0x01)')
    parser.add_argument('--signed', action='store_true',
                        help='own-CA certificate hook: build CDT (0x06) + '
                             'SignedMultiTable (0x07) with openatsc3_pki and '
                             'sign the SLT')
    parser.add_argument('--ca-dir', default=None,
                        help='existing openatsc3-pki CA tree for --signed '
                             '(default: create a throwaway CA)')
    parser.add_argument('--slt-bsid', type=int, default=None,
                        help='bsid the default SLT advertises (default --bsid)')
    parser.add_argument('--frame-interval', type=int, default=0,
                        help='bootstrap frame_interval (A/321)')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    tables = []
    slt = None
    if not args.no_slt or args.signed:
        slt = _default_slt(args.slt_bsid or args.bsid)
    if args.signed:
        tables = _signed_tables(args, slt) + tables
    if slt is not None:
        tables = tables + [slt]
    for spec_str in args.lls:
        table_id, path = spec_str.split(':', 1)
        with open(path, 'rb') as fh:
            body = fh.read()
        tables.append(bytes([int(table_id, 0), 0, 0, 1]) + body)
    if not tables:
        tables = [slt] if slt else []

    frame, plp = transmit.build_lls_frame(
        tables, bsid=args.bsid, plp_id=args.plp,
        frame_interval=args.frame_interval)
    _write_iq(args.out, frame.iq, args.fmt)
    print(f"Wrote {args.out}: {len(frame.iq)} samples "
          f"({len(frame.iq) / spec.MAIN_RATE_HZ * 1000:.3f} ms), "
          f"structure {frame.structure}, PLP {plp.plp_id}, "
          f"{plp.size} cells, {len(tables)} LLS table(s), fmt {args.fmt}")
    print(f"  decode with: atsc3-decode {args.out} --rate {spec.MAIN_RATE_HZ:.0f} "
          f"--fmt {args.fmt} --plp {args.plp}")
    return 0


def _default_slt(bsid: int) -> bytes:
    """A minimal A/331 SLT table payload (header included), gzip-compressed."""
    import gzip
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<SLT xmlns="tag:atsc.org,2016:XMLSchemas/ATSC3/Delivery/SLT/1.0/" '
        f'bsid="{bsid}"><Service serviceId="1" majorChannelNo="32" '
        'minorChannelNo="1" shortServiceName="OpenATSC3">'
        '<BroadcastSvcSignaling slsProtocol="1" '
        'slsDestinationIpAddress="239.255.32.1" slsDestinationUdpPort="8321"/>'
        '</Service></SLT>'
    ).encode()
    return bytes([0x01, 0, 0, 1]) + gzip.compress(xml)


def _signed_tables(args, slt: bytes) -> list:
    """Build the own-CA CDT (0x06) + SignedMultiTable (0x07) (A/360)."""
    import datetime as dt
    import gzip
    import tempfile

    pki = __import__('openatsc3_pki', fromlist=['ca', 'cdt', 'keys', 'ocsp'])
    from openatsc3_pki import ca, cdt, keys, ocsp
    from openatsc3_pki.x509 import (
        SubjectInfo, issue_ocsp_responder, issue_signaling_signer)

    ca_dir = args.ca_dir
    if ca_dir is None:
        ca_dir = tempfile.mkdtemp(prefix='atsc3-tx-ca-', dir='out')
    a = ca.CertificateAuthority(ca_dir)
    try:
        a.load_root()
    except Exception:
        a.init_root(common_name="OpenATSC3 Root CA")
        a.issue_issuing()
    signer = a.issue_broadcaster("OpenATSC3", (args.bsid,))
    signer_key = a.load_broadcaster_key("OpenATSC3")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    cdt_key = keys.generate()
    cdt_cert = issue_signaling_signer(
        SubjectInfo(common_name="OpenATSC3-CDT-Signer",
                    organizational_unit="ATSC Broadcast Signaling Signer",
                    organization="OpenATSC3"),
        cdt_key, a.load_issuing(), a.load_issuing_key(), (args.bsid,))
    resp_key = keys.generate()
    resp_cert = issue_ocsp_responder(
        SubjectInfo(common_name="OpenATSC3 OCSP",
                    organizational_unit="ATSC OCSP Responder",
                    organization="OpenATSC3"),
        resp_key, a.load_issuing(), a.load_issuing_key())
    store = ocsp.StatusStore()

    def resp(cert, issuer):
        return ocsp.respond(cert, issuer, resp_cert, resp_key, store,
                            this_update=now,
                            next_update=now + dt.timedelta(hours=20))

    table = cdt.build(
        chain=[a.load_issuing().der()], signing_cert=signer,
        cdt_signer_cert=cdt_cert, cdt_signer_key=cdt_key,
        ocsp_responses=[resp(a.load_issuing(), a.load_issuing()),
                        resp(signer, a.load_issuing()),
                        resp(cdt_cert, a.load_issuing())],
        signing_time=now)
    smt = cdt.signed_multitable(
        [cdt.LlsTable(0x01, 1, gzip.compress(_slt_xml(args.bsid))),
         cdt.LlsTable(0x03, 1, b"\x00" * 8)],
        signer, signer_key, signing_time=now)
    return [bytes([0x06, 0, 0, 1]) + table.gzip_bytes(),
            bytes([0x07, 0, 0, 1]) + smt]


def _slt_xml(bsid: int) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<SLT xmlns="tag:atsc.org,2016:XMLSchemas/ATSC3/Delivery/SLT/1.0/" '
        f'bsid="{bsid}"><Service serviceId="1" majorChannelNo="32" '
        'minorChannelNo="1" shortServiceName="OpenATSC3">'
        '<BroadcastSvcSignaling slsProtocol="1" '
        'slsDestinationIpAddress="239.255.32.1" slsDestinationUdpPort="8321"/>'
        '</Service></SLT>'
    ).encode()


def _write_iq(path: str, iq, fmt: str) -> None:
    """Write complex IQ to a cs8/cs16 interleaved or cf32 file."""
    import numpy as np
    peak = float(np.max(np.abs(iq))) if len(iq) else 1.0
    if fmt == 'cf32':
        data = np.stack([iq.real, iq.imag], axis=1).astype(np.float32)
    else:
        scale = (2 ** 7 - 1) / peak if fmt == 'cs8' else (2 ** 15 - 1) / peak
        dtype = np.int8 if fmt == 'cs8' else np.int16
        data = np.stack([np.round(iq.real * scale),
                         np.round(iq.imag * scale)], axis=1).astype(dtype)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data.tofile(path)


def main(argv=None):
    return decode_main(argv)


if __name__ == '__main__':
    sys.exit(main())
