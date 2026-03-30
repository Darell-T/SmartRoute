# JARVIS — Fix Partial Suspension Reasoning

## Problem

When a service alert says "No Q between Prospect Park and 96 St", JARVIS is treating the entire Q line as unusable. In reality, the Q still runs normally outside the suspended segment. For example, if the rider is at Church Avenue (which is south of Prospect Park), the Q is still running from Church Avenue to Prospect Park -- that segment is not suspended.

JARVIS told the rider not to board the Q at Church Avenue at all, when in reality the Q is still running from Church Avenue toward Prospect Park -- that segment is outside the suspension. JARVIS should use working segments of a line before falling back to shuttles or alternatives.

## What to do

In `app/services/ai_advisor.py`, update the `SYSTEM_PROMPT`. Find the section about service alerts and replace/expand the guidance with the following. Do not rewrite unrelated sections of the prompt.

### Add this to the service alerts guidance:

```
HANDLING SERVICE ALERTS — think step by step:

Service alerts describe disruptions on specific segments of a line, not necessarily the entire line. When you see an alert like "No Q between Prospect Park and 96 St", this means:
- The Q is STILL RUNNING on segments outside that range (south of Prospect Park and north of 96 St).
- Stations between Prospect Park and 96 St on the Q line are the affected zone.

When a partial suspension affects the rider's route, reason through it in this order:
1. Is the rider's origin station inside or outside the suspended segment?
2. Is the rider's destination station inside or outside the suspended segment?
3. If the origin is outside the suspended zone, the rider CAN board the train normally and ride it to the edge of the suspension.
4. From the edge of the suspension, suggest the best way to continue: shuttle bus if available, transfer to another line, or alternative route entirely.
5. Always route the rider to their ACTUAL destination. Never substitute a closer stop or shuttle terminus as the destination. If a shuttle only goes partway, explain what the rider should do after the shuttle.

Example reasoning for "No Q between Prospect Park and 96 St" with origin at Church Avenue going to Barclays Center:
- Church Avenue is south of Prospect Park, so the Q is running there normally.
- Atlantic Ave-Barclays Center is north of Prospect Park but is served by many other lines and is the shuttle terminus.
- Option A: Take the Q from Church Avenue toward Prospect Park, then take the shuttle bus to Atlantic Ave-Barclays Center.
- Option B: If another line from a nearby station goes directly to Atlantic Ave-Barclays Center without hitting the suspended zone, that may be faster.
- Recommend whichever gets the rider there quickest, but always explain the service change.

Never tell the rider to avoid a line entirely when only a segment is suspended. Use the working segments when they help.
```

### Also add this general routing rule if not already present:

```
Always route the rider to their actual requested destination, not to an intermediate transfer point or shuttle terminus. The answer is not complete until the rider knows how to get from where they are to where they asked to go.
```

## Constraints

- Only modify the `SYSTEM_PROMPT` string in `ai_advisor.py`.
- Do not touch `stream_recommendation`, `_build_payload`, `_MODEL_PRIORITY`, or retry logic.
- Do not change any other files.
- Keep the existing JARVIS personality and tone intact -- just add the routing intelligence.
