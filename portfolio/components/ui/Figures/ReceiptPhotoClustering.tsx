import React, { useEffect, useState } from "react";
import { api } from "../../../services/api";
import { Image, ImagesApiResponse } from "../../../types/api";

const ReceiptPhotoClustering = () => {
  const [images, setImages] = useState<Image[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .fetchImagesByType("PHOTO", 5)
      .then((resp: ImagesApiResponse) => {
        setImages(resp.images);
      })
      .catch((err: Error) => {
        setError(err.message);
      });
  }, []);

  if (error) {
    return <div style={{ color: "red" }}>Error: {error}</div>;
  }

  return (
    <div>
      <h3>Sample Photo Images</h3>
      <ul>
        {images.map((img) => (
          <li key={img.image_id}>{img.image_id}</li>
        ))}
      </ul>
    </div>
  );
};

export default ReceiptPhotoClustering;
