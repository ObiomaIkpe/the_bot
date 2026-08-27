import { Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { BrokerCredentialOut } from "../api/types";

/** Default post-login landing: a brand new customer with zero broker
 * accounts connected goes straight to the Broker Connection first-run
 * flow instead of an Overview page with nothing to show. Once a
 * credential exists, this always sends them to Overview -- navigation
 * itself stays unrestricted either way, this only decides the default. */
export function RootRedirect() {
  const { data, isLoading } = useQuery({
    queryKey: ["broker-credentials"],
    queryFn: () => apiClient.get<BrokerCredentialOut[]>("/broker-credentials"),
  });

  if (isLoading) return <p>Loading...</p>;
  if (data && data.length === 0) return <Navigate to="/broker-credentials" replace />;
  return <Navigate to="/overview" replace />;
}
