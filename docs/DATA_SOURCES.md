# Public footage search — 3 November 2026

Requested: at least one hour of high-quality real video, with multiple
synchronized cameras of the same market covering shelves, entrance/exit and
checkout. No source below has been verified to satisfy all those conditions
with a direct public download. Short clips must not be looped or concatenated
to pretend to meet that requirement.

| Source | Actual release / suitability | Access |
|---|---|---|
| [CARR](https://doi.org/10.7910/DVN/CY24UB) | Real 1280x720 retail footage from 13 stores; activity clips, not a synchronized hour. Six short contiguous examples plus a 59.8s contiguous sequence installed. Faces may be publisher-blurred. | Public research release, CC BY-NC 4.0. Acquisition receipts, original indices and video manifests in `data/public_retail_hd/`. |
| [MMPTRACK](https://iccv2021-mmp.github.io/subpage/dataset.html) | Synchronized views with calibration; retail has six cameras, but published RGB is 640x320. Staged environments. | Signed terms and email request. No request sent or agreement signed. |
| [MM-Store](https://zenodo.org/records/17494546) | Six-camera retail collection described, but checked record publishes only a description text file, not footage. | Metadata available; not usable footage as checked. |
| [NVIDIA PhysicalAI-SmartSpaces](https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces) | 1080p, synchronized, retail among scenes; **synthetic** Omniverse/Cosmos video. Useful for multi-camera engineering, not evidence of real-store pickup accuracy. | Public release, CC BY 4.0. Not substituted for real footage. |
| [MEVA KF1](https://mevadata.org/resources/README-meva-kf1-data.html) | Hundreds of hours from synchronized cameras at a staged facility, commonly five-minute files. Real camera pixels, but not verified market/checkout coverage. | Direct public S3 download. Bucket object listing verified and sample inventory saved in `runs/demo_live_v2/`. Not relabelled as a supermarket. |
| [StandardAI Tracks](https://huggingface.co/datasets/standard-cognition/Tracks) | Large real-retail trajectory collection and an hour evaluation; published trajectories/poses are not raw pixel video for this detector. | Public sample; broader data by request. |
| RetailAction | Short dual-view action exports with motion-selected frames. Good source for action training, unsuitable for elapsed-time dwell or continuous customer routes. | Retained locally with original provenance/terms. |

Best route to the requested full-market demonstration: licensed synchronized
recordings from a willing retailer or approved dataset access. A useful capture
package has fixed-camera MP4s, common clock offsets, one continuous hour per
view, shelf/door/cashier coverage, a camera floor plan and independently labelled
pickup intervals. The project currently uses public datasets only. No unsecured
live CCTV feeds or privately obtained recordings were used.

The installed seven clips test the real integration. They do not establish
cross-camera tracking, full-store coverage, independent pickup precision,
checkout completion or theft detection.
