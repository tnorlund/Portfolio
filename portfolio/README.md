# Tyler Norlund's Portfolio

![Next.js](https://img.shields.io/badge/Next.js-15.3-black?logo=next.js)
![TypeScript](https://img.shields.io/badge/TypeScript-5.8-blue?logo=typescript)
![React](https://img.shields.io/badge/React-19.1-61dafb?logo=react)
![License](https://img.shields.io/badge/License-Private-red)

## 🚀 Performance Metrics

![Lighthouse Score](https://img.shields.io/badge/Lighthouse-85%2F100-yellow)
![Desktop Performance](https://img.shields.io/badge/Desktop-100%2F100-brightgreen)
![Mobile Performance](https://img.shields.io/badge/Mobile-85%2F100-green)

### Core Web Vitals (Production)
| Metric | Desktop | Mobile | Target |
|--------|---------|--------|--------|
| **LCP** | 1.2s ✅ | 2.3s ✅ | < 2.5s |
| **FID** | 12ms ✅ | 45ms ✅ | < 100ms |
| **CLS** | 0.02 ✅ | 0.05 ✅ | < 0.1 |
| **FCP** | 0.8s ✅ | 1.6s ✅ | < 1.8s |
| **TTFB** | 200ms ✅ | 400ms ✅ | < 800ms |

## 📊 Test Coverage

![Coverage](https://img.shields.io/badge/Coverage-42%25-yellow)
![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen)

| Type | Coverage | Status |
|------|----------|--------|
| **Statements** | 42.25% | ✅ Above threshold (40%) |
| **Branches** | 37.28% | ✅ Above threshold (35%) |
| **Functions** | 36.66% | ✅ Above threshold (35%) |
| **Lines** | 41.63% | ✅ Above threshold (40%) |

### Test Breakdown
- **Unit Tests**: 37 test files covering components and utilities
- **Integration Tests**: API and component interaction tests
- **Performance Tests**: Automated Lighthouse CI on PRs

## 🎨 Features

- **Dynamic Image Galleries**: Optimized ImageStack and ReceiptStack components with progressive loading
- **Advanced Performance Monitoring**: Real-time development overlay showing Core Web Vitals
- **Responsive Design**: Mobile-first approach with adaptive layouts
- **Image Optimization**: Automatic WebP/AVIF conversion with fallbacks
- **API Integration**: AWS Lambda + DynamoDB backend with CloudFront CDN

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Next.js App   │────▶│ CloudFront CDN   │────▶│   S3 Static     │
│  (React + TS)   │     │ (Edge Caching)   │     │    Assets       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                                                 │
         ▼                                                 ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   API Gateway   │────▶│ Lambda Functions │────▶│   DynamoDB      │
│   (HTTP API)    │     │  (Python 3.12)   │     │  (NoSQL DB)     │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## 🚀 Getting Started

### Prerequisites
- Node.js 18+ 
- npm or yarn
- AWS credentials (for API access)

### Installation

```bash
# Clone the repository
git clone https://github.com/tnorlund/Portfolio.git
cd Portfolio/portfolio

# Install dependencies
npm install

# Set up environment variables
cp .env.example .env.local
# Edit .env.local with your configuration
```

### Development

```bash
# Start development server
npm run dev

# Run tests
npm run test           # Unit tests
npm run test:watch     # Watch mode
npm run test:coverage  # With coverage report

# Code quality
npm run lint           # ESLint
npm run type-check     # TypeScript checking

# Performance analysis
npm run analyze        # Bundle analyzer
npm run analyze:bundle # CLI bundle report
```

## 🧪 Testing Strategy

### Test Commands

```bash
# Unit tests (fast, run frequently)
npm run test

# Test coverage
npm run test:coverage

# Type checking
npm run type-check

# Linting
npm run lint

# All checks (pre-push)
npm run test && npm run type-check && npm run lint
```

### Performance Testing

```bash
# Analyze bundle size
npm run analyze

# Generate bundle report
npm run analyze:bundle

# Run Lighthouse locally (requires Chrome)
npx lighthouse http://localhost:3000 --view
```

### Testing Philosophy

1. **Unit Tests**: Fast feedback on component behavior
2. **Integration Tests**: Verify feature functionality
3. **Performance Tests**: Prevent regression
4. **Manual Testing**: Complex user interactions

## 📈 Performance Monitoring

### Development Mode

The app includes a performance overlay in development showing:
- Real-time FPS counter
- Core Web Vitals (LCP, FID, CLS, FCP, TTFB)
- Memory usage visualization
- Component render times
- API call durations

### Production Monitoring

- CloudFront metrics for CDN performance
- Lambda function duration tracking
- Client-side Web Vitals collection (planned)

## 🎯 Performance Optimizations

### Implemented Optimizations

1. **Dynamic Imports**: Heavy components loaded on-demand
2. **Progressive Loading**: ImageStack/ReceiptStack load 6 items initially
3. **Image Format Detection**: Automatic AVIF → WebP → JPEG fallback
4. **Intersection Observer**: Optimized viewport detection
5. **Bundle Splitting**: Vendor and common chunks optimization
6. **CDN Caching**: 30-day cache for static assets
7. **Compression**: Brotli/gzip for all text assets

### Performance Budgets

| Resource | Budget | Current |
|----------|--------|---------|
| JavaScript (gzipped) | < 300KB | ~250KB ✅ |
| CSS (gzipped) | < 50KB | ~30KB ✅ |
| Images | Lazy loaded | ✅ |
| Total Page Weight | < 1MB | ~800KB ✅ |

## 🔧 Configuration

### Next.js Configuration
- Static export enabled
- Image optimization configured
- Bundle analyzer integrated
- Compression enabled

### Test Configuration
- Jest with Next.js preset
- React Testing Library
- Coverage thresholds enforced
- Fast refresh in development

## 📝 Documentation

- [API Documentation](../infra/API_DOCUMENTATION.md)
- [Performance monitoring utilities](docs/utils/performance/README.md)

## 🚢 Deployment

The site is automatically deployed to AWS S3 + CloudFront on merge to main.

```bash
# Build for production
npm run build

# Preview production build
npm run start
```

## 🤝 Contributing

This is a private portfolio project. For any questions or suggestions, please contact Tyler Norlund.

## 📄 License

This project is private and proprietary.

---

**Last Updated**: January 2025  
**Performance Score**: 85/100 (Mobile) | 100/100 (Desktop) 🎉