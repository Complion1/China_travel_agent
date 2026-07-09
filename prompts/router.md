# Router Prompt

Classify the user request into one route:

- `travel_plan`: create a new end-to-end itinerary.
- `adjust_plan`: modify an existing itinerary.
- `place_query`: ask about attractions, hotels, restaurants, or places.
- `route_query`: ask about transportation between places.
- `city_recommend`: user wants destination ideas but has no fixed city.
- `chat`: ordinary conversation.

Return structured routing metadata. Do not plan the trip in the router.
