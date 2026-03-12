"""
Flask service for controlling the HDMI matrix via HTTP API.
"""

from flask import Flask, request, jsonify

from hdmi_matrix import HDMIMatrix

app = Flask(__name__)

# Global matrix instance (can be enhanced to support multiple connections)
matrix = None


def get_matrix() -> HDMIMatrix:
    """Get or create the global matrix connection."""
    global matrix
    if matrix is None:
        raise RuntimeError("Matrix not initialized. Call /init first.")
    return matrix


@app.before_request
def initialize_matrix():
    """Initialize the matrix connection on first request."""
    global matrix
    if matrix is None:
        try:
            matrix = HDMIMatrix()
        except Exception as e:
            return jsonify({"error": f"Failed to initialize matrix: {str(e)}"}), 500


@app.route('/set-output-input', methods=['POST'])
def set_output_input():
    """
    Set a video output to a single input.

    Request JSON:
        {
            "output": "A" or "B",
            "input": 1, 2, 3, or 4 (or "U"/"D" to step up/down)
        }

    Response:
        {"status": "success", "response": "device response"}
        or
        {"error": "error message"} (400 or 500)
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        output = data.get('output')
        inp = data.get('input')

        if output is None or inp is None:
            return jsonify({"error": "Missing required fields: 'output' and 'input'"}), 400

        matrix = get_matrix()
        response = matrix.set_output_input(output, inp)

        return jsonify({"status": "success", "response": response}), 200

    except ValueError as e:
        # Validation error from HDMIMatrix
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # Other errors (serial port, etc.)
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Endpoint not found"}), 404


if __name__ == '__main__':
    # Run the Flask app on localhost:5000
    app.run(host='0.0.0.0', port=5000, debug=False)
