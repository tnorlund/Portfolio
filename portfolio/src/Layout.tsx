import React from "react";
import "./App.css"; // Ensure your .container CSS is imported

interface LayoutProps {
  children: React.ReactNode;
}

function Layout({ children }: LayoutProps) {
  return <div className="container">{children}</div>;
}

export default Layout;