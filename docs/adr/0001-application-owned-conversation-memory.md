# Application-owned conversation memory

SmartRoute keeps the complete transcript and authoritative session memory in its own 30-minute conversation session, while supplying the conversational model with a bounded context projection. A New Trip ends both the conversation and trip state; explicitly saved Rider Profile data may survive that boundary. Provider-managed conversation state and a generic model-owned context map were rejected because canonical trip facts, freshness, privacy, provider independence, and reset behavior must remain under SmartRoute's control.
