# Keep queue evidence inside place discovery

SmartRoute needs optional venue wait facts without a ninth model-visible tool and without treating those facts as itinerary math. Queue evidence therefore stays inside `discover_places` and `present_places`. Google Places remains the authority for physical venue identity, branch, and open status. Damn Lines is consulted only through a manual exact Google Place ID registry, and missing coverage stays unknown rather than becoming a short-line inference. Rider-facing numbers, timestamps, coverage wording, and trusted source URLs are emitted by `present_places`. The conversation may show them. Maps, route cards, route steps, and canonical itinerary arithmetic may not.

## Considered options

- A dedicated queue capability. Rejected because it would expand the eight-capability contract and invite the model to fetch wait data as a separate goal.
- Fuzzy brand matching onto Damn Lines slugs. Rejected because a wrong branch would present another venue's wait as if it were this one.
- Putting wait facts on map markers or route cards. Rejected because those surfaces own transit geometry and canonical itinerary facts, not conversational place context.
