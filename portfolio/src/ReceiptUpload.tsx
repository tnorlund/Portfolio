import { useState } from "react";

function ReceiptUpload() {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const [dragging, setDragging] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFiles((prevFiles) => [...prevFiles, ...Array.from(e.target.files!)]);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files) {
      setFiles((prevFiles) => [
        ...prevFiles,
        ...Array.from(e.dataTransfer.files),
      ]);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  };

  const handleDragLeave = () => {
    setDragging(false);
  };

  const uploadToS3 = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setMessage("");

    try {
      for (const file of files) {
        const filename = encodeURIComponent(file.name);
        const res = await fetch(
          `https://dev-upload.tylernorlund.com/get-presigned-url?filename=${filename}&contentType=${encodeURIComponent(
            file.type
          )}`
        );
        const { url, key } = await res.json();

        const upload = await fetch(url, {
          method: "PUT",
          headers: {
            "Content-Type": file.type,
          },
          body: file,
        });

        if (upload.ok) {
          const submit = await fetch(
            "https://dev-upload.tylernorlund.com/submit-job",
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                s3_key: key,
                original_filename: file.name,
                content_type: file.type,
              }),
            }
          );

          if (!submit.ok) {
            throw new Error(`Job submission failed for file ${file.name}`);
          }
        } else {
          throw new Error(`Upload failed for file ${file.name}`);
        }
      }
      setMessage(`Upload successful: ${files.map((f) => f.name).join(", ")}`);
      setFiles([]);
    } catch (err) {
      console.error(err);
      setMessage("Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <h2>Upload Receipt</h2>
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        style={{
          border: "2px dashed #888",
          padding: "2rem",
          marginBottom: "1rem",
          textAlign: "center",
          backgroundColor: dragging ? "#eee" : "#fff",
        }}
      >
        {files.length > 0 ? (
          <ul style={{ listStyleType: "none", padding: 0, margin: 0 }}>
            {files.map((file, index) => (
              <li key={index}>{file.name}</li>
            ))}
          </ul>
        ) : (
          <p>Drag and drop receipt images here, or click to select</p>
        )}
        <input
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          style={{ display: "none" }}
          id="file-input"
          multiple
        />
        <label
          htmlFor="file-input"
          style={{
            cursor: "pointer",
            color: "blue",
            textDecoration: "underline",
          }}
        >
          Browse
        </label>
      </div>
      <button onClick={uploadToS3} disabled={files.length === 0 || uploading}>
        {uploading ? "Uploading..." : "Upload to S3"}
      </button>
      <p>{message}</p>
    </div>
  );
}

export default ReceiptUpload;
