export interface AgentThought {
  agent: string;
  content: string;
}

export interface ParsedAgentStream {
  reply: string;
  thoughts: AgentThought[];
  agent?: string;
}

const markerPattern = /\[(THOUGHT|REPLY|SIGNAL|STATE)\](?:\[([^\]]+)\])?/g;
const protocolMarkers = ["[THOUGHT]", "[REPLY]", "[SIGNAL]", "[STATE]"];

function removeTrailingMarkerFragment(value: string): string {
  const bracketIndex = value.lastIndexOf("[");
  if (bracketIndex < 0) return value;
  const tail = value.slice(bracketIndex);
  return protocolMarkers.some((marker) => marker.startsWith(tail))
    ? value.slice(0, bracketIndex)
    : value;
}

/** Parse the internal Agent stream protocol into user-facing content. */
export function parseAgentStream(raw: string): ParsedAgentStream {
  const markers = [...raw.matchAll(markerPattern)];
  if (markers.length === 0) {
    const reply = protocolMarkers.some((marker) => marker.startsWith(raw.trim()))
      ? ""
      : removeTrailingMarkerFragment(raw);
    return { reply, thoughts: [] };
  }

  const thoughts: AgentThought[] = [];
  const replyParts: string[] = [];
  let replyAgent: string | undefined;

  markers.forEach((match, index) => {
    const type = match[1];
    const agent = match[2] || "处理节点";
    const start = (match.index || 0) + match[0].length;
    const end = index + 1 < markers.length ? markers[index + 1].index : raw.length;
    const content = removeTrailingMarkerFragment(raw.slice(start, end)).trim();

    if (type === "THOUGHT" && content) {
      thoughts.push({ agent, content });
    } else if (type === "REPLY") {
      replyAgent = agent;
      if (content) replyParts.push(content);
    }
  });

  return {
    reply: replyParts.join("\n").trim(),
    thoughts,
    agent: replyAgent
  };
}
