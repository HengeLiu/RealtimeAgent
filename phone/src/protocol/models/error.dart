class ErrorModel {
  const ErrorModel({
    required this.errorCode,
    required this.errorMessage,
    required this.errorType,
    required this.source,
    required this.retryable,
    this.details = const {},
  });

  final String errorCode;
  final String errorMessage;
  final String errorType;
  final String source;
  final bool retryable;
  final Map<String, dynamic> details;

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'error_code': errorCode,
      'error_message': errorMessage,
      'error_type': errorType,
      'source': source,
      'retryable': retryable,
      'details': details,
    };
  }
}

