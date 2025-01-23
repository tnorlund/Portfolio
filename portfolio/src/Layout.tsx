import React from "react";
import "./App.css"; // Ensure your .resume-container CSS is imported

interface LayoutProps {
  children: React.ReactNode;
}

function Layout({ children }: LayoutProps) {
  return <div className="resume-container">{children}</div>;
}

export default Layout;