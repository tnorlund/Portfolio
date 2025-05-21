import React from 'react';
import './App.css';

interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  return <div className="container">{children}</div>;
}
