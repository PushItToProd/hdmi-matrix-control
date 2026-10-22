# 4PET0402QMS 4x2 HDMI Matrix Control

Python utility for controlling a 4PET0402QMS 4x2 HDMI matrix over USB.

## Web controls

Open the service's root URL (for example, `http://localhost:5000/`) to view
input links and output routing, switch either output to an input, or select
Output A's multiview layout. The interface fits narrow phones, tablets, and
laptops. HTMX is served locally, so the browser needs no internet connection.

The page reads the device on its first visit if the service has no retained
state. It checks retained service state every five seconds without sending
status commands to the matrix. Successful routing requests through either the
web controls or the JSON API update this state immediately. Requested routes
are labeled as unverified; input links and resolutions come from device readings.
The layout form preserves edits while the displayed state refreshes.

Use **Refresh from device** to observe changes made with a physical remote or
front panel and verify requested routes. UI refreshes share a five-second
cooldown across browsers, including failed reads. Existing `/status` callers
retain their cache and `max_age` behavior; explicit API verification can still
request more frequent reads. Output B's copy mode is displayed but is not
changed by the routing controls.

## API

To enable quick switching of both outputs, the HTTP service supports batched
routing with `POST /apply`. To help clients avoid stepping on each other, states
reported from `GET /status` are versioned and requests may include an
`if_version` parameter so changes will only be applied if the given version is
in use. See [the API and rollout notes](notes/apply.md) for request shapes,
timing, verification, and legacy endpoint compatibility.

Tested on a "PORTTA VALUE HDMI 4x2 Matrix, 4K30Hz Quad Multi Viewer", but there appear to be other brands selling 4PET0402QMS HDMI matrices as well.

> [!CAUTION]
> This repo is not officially supported by, affiliated with, or endorsed by PORTTA or any other vendor who sells or manufactures 4PET0402QMS HDMI matrices.
