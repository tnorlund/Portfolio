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
    title: "Freelance",
    business: "tnor",
    location: "Westlake Village, CA",
    dateRange: "Sept 2020 - Now",
    bullets: [
      "Built full-stack applications to process and visualize large datasets using Python, React, and SQL databases",
      "Established CI/CD best practices to automate and streamline production deployments",
      "Integrated PostgreSQL and DynamoDB to support scalable, data-driven applications",
    ],
  },
  {
    title: "Data Engineer",
    business: "Dollar Shave Club",
    location: "Marina Del Rey, CA",
    dateRange: "May 2021 - Feb 2024",
    bullets: [
      "Developed and optimized scalable data pipelines, handling large data streams for data-driven decisions",
      "Designed and deployed mission-critical applications to enhance data integration and streamline processes",
      "Collaborated on system architecture and deployment strategies, improving data processing efficiency by 80%",
      "Leveraged Apache Kafka and Spark to manage high-volume, real-time data, boosting system reliability",
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
