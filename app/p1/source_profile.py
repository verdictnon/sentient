"""Explicit media semantics: decoded playback time is not always physical time."""

PHYSICAL_FIELDS = (
    'inward_crossings', 'outward_crossings', 'complete_observed_dwell_ms',
    'complete_observed_dwell_seconds', 'censored_observed_dwell_ms',
    'complete_visits', 'censored_visits', 'queue_completed_visits',
    'queue_censored_visits', 'queue_abandoned_visits', 'queue_completed_waits',
    'queue_completed_waiting_ms', 'queue_open_service_visits', 'queue_open_waits',
)


def source_profile(video):
    collection = video.get('collection_session', '')
    sparse = collection.startswith('retailaction/')
    carr = collection.startswith('carr/research/')
    width, height = video['frame_dims_px']
    return {
        'dataset': 'RetailAction' if sparse else 'CARR' if carr else 'recorded_video',
        'source_kind': 'sparse_clip' if sparse else 'continuous',
        'physical_time_known': not sparse,
        'timing_basis': ('Motion-selected research frames; original elapsed time unavailable' if sparse else
            'Contiguous publisher frame indices / declared 15 fps; derived video PTS' if carr else
            'Decoded source video PTS; capture UTC remains separate'),
        'resolution_px': [width, height],
        'frame_count': video['frame_count'],
        'source_is_hd': width >= 1280 and height >= 720,
        'physical_analytics_supported': not sparse,
        'recommended_detector_sampling': 'every_source_frame' if sparse else 'elapsed_source_time',
        'action_model_status': 'experimental_transfer_review_only',
        'warnings': ([
            'This clip is a motion-sampled action research export, not continuous CCTV.',
            'Playback seconds cannot establish customer dwell, routes, or entrance counts.',
        ] if sparse else []) + ([
            'CARR labels describe six coarse activities of one designated participant; no automatic take/put mapping.',
            'Research data, CC BY-NC 4.0; source cadence is publisher-declared, not recovered camera timestamps.',
        ] if carr else []),
    }


def interpret_summary(summary, source_kind):
    if source_kind != 'sparse_clip':
        return summary | {'physical_analytics_supported': True}
    # Retain deterministic geometric calculations as diagnostics, never customer
    # metrics. Null means unavailable, while zero would assert an observed zero.
    if summary.get('physical_analytics_supported') is False:
        return summary
    raw = {key: summary[key] for key in PHYSICAL_FIELDS if key in summary}
    return summary | {key: None for key in raw} | {
        'physical_analytics_supported': False,
        'physical_analytics_reason': 'Sparse action export has unknown original elapsed time and continuity',
        'raw_geometry_diagnostics': raw,
    }
