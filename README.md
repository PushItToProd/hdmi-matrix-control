# 4PET0402QMS 4x2 HDMI Matrix Control

Python utility for controlling a 4PET0402QMS 4x2 HDMI matrix over USB.

To enable quick switching of both outputs, the HTTP service supports batched
routing with `POST /apply`. To help clients avoid stepping on each other, states
reported from `GET /status` are versioned and requests may include an
`if_version` parameter so changes will only be applied if the given version is
in use. See [the API and rollout notes](notes/apply.md) for request shapes,
timing, verification, and legacy endpoint compatibility.

Tested on a "PORTTA VALUE HDMI 4x2 Matrix, 4K30Hz Quad Multi Viewer", but there appear to be other brands selling 4PET0402QMS HDMI matrices as well.

> [!CAUTION]
> This repo is not officially supported by, affiliated with, or endorsed by PORTTA or any other vendor who sells or manufactures 4PET0402QMS HDMI matrices.
