import re


def _token(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
        return value
    return "unknown"


def failure_details(error: Exception) -> str:
    """Describe a failure without logging request payloads or service messages."""
    fields = [f"error_type={_token(type(error).__name__)}"]
    response = getattr(error, "response", None)

    if not isinstance(response, dict):
        return " ".join(fields)

    operation = getattr(error, "operation_name", None)
    if operation is not None:
        fields.append(f"aws_operation={_token(operation)}")

    service_error = response.get("Error")
    if isinstance(service_error, dict) and "Code" in service_error:
        fields.append(f"aws_error_code={_token(service_error['Code'])}")

    metadata = response.get("ResponseMetadata")
    if isinstance(metadata, dict):
        if "RequestId" in metadata:
            fields.append(f"aws_request_id={_token(metadata['RequestId'])}")

        status = metadata.get("HTTPStatusCode")
        if type(status) is int and 100 <= status <= 599:
            fields.append(f"aws_http_status={status}")

    return " ".join(fields)
