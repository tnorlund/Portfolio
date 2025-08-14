# Component Structure

This directory contains all React components organized by their purpose:

## Directory Structure

- `ui/` - Reusable UI components

  - `LoadingSpinner/`
  - `Logos/` (ReactLogo, TypeScriptLogo, etc.)
  - `Icons/` (GithubLogo, etc.)

- `layout/` - Layout components

  - `Layout.tsx`
  - `App.tsx` (to be converted to a layout component)

- `features/` - Feature-specific components

  - `receipt/` (Receipt.tsx, ReceiptStack.tsx)
  - `embedding/` (EmbeddingCoordinate.tsx, EmbeddingExample.tsx)
  - `visualization/` (Diagram.tsx, UploadDiagram.tsx)
  - `bounding-box/` (boundingBox.tsx)
  - `data/` (DataCounts.tsx)

- `shared/` - Shared components and utilities
  - `interfaces.ts`
  - Common utilities and hooks

## Migration Steps

1. Move each component to its appropriate directory
2. Update imports in all files
3. Convert any CSS files to CSS Modules
4. Update component exports
5. Remove old component locations

## Component Categories

### UI Components

- LoadingSpinner
- ReactLogo
- TypeScriptLogo
- GithubLogo
- Pulumi
- OpenAI

### Layout Components

- Layout
- App (to be converted)

### Feature Components

- Receipt
- ReceiptStack
- EmbeddingCoordinate
- EmbeddingExample
- Diagram
- UploadDiagram
- boundingBox
- DataCounts
- GooglePlaces
- HuggingFace
- Pinecone
- Resume

### Shared

- interfaces.ts
- Any shared utilities or hooks
