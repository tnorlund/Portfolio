import React, { useEffect, useState, useMemo } from "react";
import { api } from "../../../services/api";
import { AddressSimilarityResponse } from "../../../types/api";
import {
  detectImageFormatSupport,
} from "../../../utils/imageFormat";
import SimpleCroppedAddress from "./SimpleCroppedAddress";

/**
 * Simple wrapper component that fetches address similarity data
 * and displays just the original address using SimpleCroppedAddress.
 */
const SimpleAddressDisplay: React.FC = () => {
  const [data, setData] = useState<AddressSimilarityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch address similarity data
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await api.fetchAddressSimilarity();
        setData(response);
      } catch (err) {
        console.error("Failed to fetch address similarity:", err);
        setError(err instanceof Error ? err.message : "Failed to load data");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#666",
        }}
      >
        Loading address...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#999",
        }}
      >
        {error || "No address data available"}
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        width: "100%",
        maxWidth: "600px",
        margin: "0 auto",
      }}
    >
      <SimpleCroppedAddress
        receipt={data.original.receipt}
        lines={data.original.lines}
        formatSupport={formatSupport}
        maxWidth="100%"
        bbox={data.original.bbox}
      />
    </div>
  );
};

export default SimpleAddressDisplay;


