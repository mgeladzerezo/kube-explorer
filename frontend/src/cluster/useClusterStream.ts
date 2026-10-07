import { useEffect, useReducer, useState } from "react";

import { clusterStreamUrl } from "../api/client";
import type { StreamMessage } from "../api/types";
import { applyMessage, emptyCluster, type ClusterState } from "./reducer";

export type ConnectionStatus = "connecting" | "live" | "reconnecting";

const MAX_RETRY_MS = 15_000;

export function retryDelay(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, MAX_RETRY_MS);
}

/**
 * Keeps a WebSocket to /ws/cluster open and folds its messages into state.
 * The server sends a full snapshot on every connect, so reconnecting needs no
 * resume logic on this side: the snapshot overwrites whatever went stale.
 */
export function useClusterStream(): { cluster: ClusterState; status: ConnectionStatus } {
  const [cluster, dispatch] = useReducer(applyMessage, emptyCluster);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");

  useEffect(() => {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let attempt = 0;
    let disposed = false;

    const connect = () => {
      socket = new WebSocket(clusterStreamUrl(window.location));
      socket.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data) as StreamMessage;
        if (message.type === "snapshot") {
          attempt = 0;
          setStatus("live");
        }
        dispatch(message);
      };
      socket.onclose = () => {
        if (disposed) return;
        setStatus("reconnecting");
        retryTimer = window.setTimeout(connect, retryDelay(attempt));
        attempt += 1;
      };
    };

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return { cluster, status };
}
