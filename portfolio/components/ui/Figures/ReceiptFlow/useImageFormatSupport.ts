import { useEffect, useState } from "react";
import { detectImageFormatSupport } from "../../../../utils/imageFormat";
import { ImageFormatSupport } from "./types";

export const useImageFormatSupport = (): ImageFormatSupport | null => {
  const [formatSupport, setFormatSupport] = useState<ImageFormatSupport | null>(null);

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  return formatSupport;
};
