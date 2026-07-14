import React from "react";
import Head from "next/head";

export interface ResumeRoleProps {
  title: string;
  business?: string;
  location?: string;
  dateRange: string;
  bullets: string[];
}

const roles: ResumeRoleProps[] = [
  {
    title: "Staff Data Engineer",
    business: "MonkeyTilt",
    location: "Las Vegas, NV",
    dateRange: "Feb 2026 - Jun 2026",
    bullets: [
      "Architected a governed, real-time data platform on ClickHouse, Kafka, Django, and Kubernetes, centralizing player, transaction, and reporting data into a trusted source of truth for finance, product, and operations",
      "Modernized executive finance reporting into a production, SQL-first pipeline, reconciling sports betting, manual adjustments, wallet balances, and test players across ClickHouse, OpenSearch, and Google Sheets",
      "Established analytics governance and lineage with dbt-managed ClickHouse layers, semantic metadata, PII classification, and OpenLineage patterns, making business logic auditable and reported metrics trustworthy",
      "Operationalized product data delivery with profile-archive, backfill, and downstream flows across OpenSearch, S3, ClickHouse, and rewards services, enabling historical replay, safer rollouts, and CI/CD",
    ],
  },
  {
    title: "Staff Data Engineer, E-commerce & Law",
    business: "TNOR",
    location: "Remote",
    dateRange: "Sept 2020 - Present",
    bullets: [
      "Architected an enterprise-scale streaming platform (Kafka/Kinesis) processing 20k+ events/second, powering real-time ML feature stores that drove product decisions",
      "Designed a high-performance search architecture with distributed vector search (FAISS + ChromaDB), achieving 100x faster telemetry retrieval for real-time anomaly detection",
      "Pioneered self-service analytics through RAG systems and MCP servers, eliminating 80% of ad-hoc reporting requests and giving non-technical stakeholders direct data access",
      "Migrated ML systems from MongoDB to PostgreSQL with zero downtime, improving query performance 75% across 50+ services",
      "Owned cloud platform architecture with infrastructure-as-code (Terraform) for EKS, Lambda, and Step Functions, reducing deployment time 90% via GitHub Actions",
    ],
  },
  {
    title: "Software Engineer, Data Platform",
    business: "Dollar Shave Club",
    location: "Marina Del Rey, CA",
    dateRange: "May 2021 - Feb 2024",
    bullets: [
      "Led an enterprise analytics platform transformation, migrating from Databricks/Scala to AWS EMR/Python, cutting operational cost 80% while improving query performance 10x for 100+ users",
      "Redesigned the ETL ecosystem with Apache Airflow, AWS EMR, and dbt, automating 500+ daily workflows at 99.9% uptime for critical business reporting",
      "Drove $10M in revenue impact through marketing analytics, integrating Braze CRM with Tableau to improve customer acquisition cost 40% and retention 25%",
      "Scaled real-time infrastructure for D2C operations with a production Kafka/Spark streaming platform for personalization and inventory optimization",
    ],
  },
  {
    title: "Data Technician",
    business: "Oculus - Contract",
    location: "Menlo Park, CA",
    dateRange: "March 2019 - Sept 2020",
    bullets: [
      "Analyzed IMU data with robotics and spatial tracking to identify key performance metrics",
      "Created and maintained local ETL pipelines for data cleaning and processing",
      "Utilized Jupyter Notebooks to explore and visualize data, producing actionable insights",
      "Leveraged Google Cloud Platform (GCP) to manage and visualize back-end data, streamlining collaboration",
    ],
  },
  {
    title: "Data Scientist",
    business: "Charles Schwab",
    location: "San Francisco, CA",
    dateRange: "Jan 2020 - May 2020",
    bullets: [
      "Implemented customer-segmentation strategies to guide executive-level decision-making",
      "Automated data extraction and labeling pipelines using NLP tools",
      "Produced interactive visualizations with D3, Tableau, and matplotlib",
      "Engineered directed and undirected graphs to optimize web-scraping operations",
    ],
  },
  {
    title: "Performance Analyst",
    business: "NVIDIA - Intern",
    location: "Santa Clara, CA",
    dateRange: "June 2017 - Dec 2017",
    bullets: [
      "Scripted automated data aggregation for performance testing, reducing manual overhead",
      "Pioneered networking techniques for massive server farm management",
      "Benchmarked C++ and Python frameworks across various hardware setups to ensure optimal performance",
      "Developed a LAMP stack front end to analyze and compare multiple project roadmaps",
    ],
  },
];

export const ResumeRole: React.FC<ResumeRoleProps> = ({
  title,
  dateRange,
  bullets,
  business,
  location,
}) => {
  return (
    <div>
      {/* Top row: Title & Date */}
      <div className="resume-box">
        <div>
          <strong>{title}</strong>
        </div>
        <div>{dateRange}</div>
      </div>

      {/* <hr> if either business or location is provided */}
      {(business || location) && <hr />}

      {/* Bottom row: business (left) + location (right) */}
      {(business || location) && (
        <div className="resume-box">
          <div>{business}</div>
          <div>{location}</div>
        </div>
      )}

      {/* Bullet list for responsibilities or achievements */}
      <ul>
        {bullets.map((bullet, index) => (
          <li key={index}>{bullet}</li>
        ))}
      </ul>
    </div>
  );
};

export default function ResumePage() {
  return (
    <div className="container">
      <Head>
        <title>Résumé | Tyler Norlund</title>
      </Head>
      <h1>Education</h1>
      <div className="resume-box">
        <div>
          MS: <strong>Data Science</strong>
        </div>
        <div>May 2020</div>
      </div>
      <hr />
      <div className="resume-box">
        <div>
          BS: <strong>Computer Engineering</strong>
        </div>
        <div>Dec 2018</div>
      </div>
      <h1>Experience</h1>
      {roles.map((role, index) => (
        <ResumeRole
          key={index}
          title={role.title}
          business={role.business}
          location={role.location}
          dateRange={role.dateRange}
          bullets={role.bullets}
        />
      ))}
    </div>
  );
}
