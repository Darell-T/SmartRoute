export interface SafeChatFailure {
  message: string;
  retryable: boolean;
}

export function safeChatFailure(status: number): SafeChatFailure {
  if (status === 400 || status === 413 || status === 422) {
    return {
      message: "SmartRoute couldn’t understand this request.",
      retryable: false,
    };
  }
  if (status === 401 || status === 403) {
    return {
      message: "SmartRoute could not authenticate the request.",
      retryable: false,
    };
  }
  if (status === 429) {
    return { message: "Too many requests. Try again shortly.", retryable: true };
  }
  if (status === 502 || status === 503) {
    return { message: "SmartRoute is temporarily unavailable.", retryable: true };
  }
  return {
    message: "SmartRoute couldn’t complete this request.",
    retryable: status >= 500,
  };
}
