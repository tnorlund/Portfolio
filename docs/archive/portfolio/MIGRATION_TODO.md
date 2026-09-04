# Next.js Migration TODO List

## 1. Project Structure Changes

- [x] Update package.json with Next.js dependencies
- [x] Create basic Next.js pages structure
- [x] Create component directory structure
- [ ] Move all components from `src/` to `components/` directory
  - [x] Set up UI components structure
  - [x] Move LoadingSpinner component
  - [x] Move ReactLogo component
  - [x] Move TypeScriptLogo component
  - [x] Move GithubLogo component
  - [x] Move Pulumi component
  - [x] Move remaining UI components
  - [x] Set up layout components structure
  - [x] Move Layout component
  - [ ] Set up and move feature components
  - [ ] Set up and move shared utilities
- [x] Create proper Next.js page structure in `pages/` directory
- [ ] Set up proper TypeScript configuration for Next.js

## 2. Routing Migration

- [x] Convert React Router routes to Next.js pages
  - [x] Create individual page components in `pages/` directory
  - [x] Replace `react-router-dom` navigation with Next.js `Link` components
  - [x] Replace `useNavigate` with Next.js `useRouter` hook
  - [x] Update any route parameters to use Next.js dynamic routes

## 3. API Integration

- [ ] Move API calls from `src/api.ts` to Next.js API routes
  - [ ] Create `pages/api/` directory
  - [ ] Convert each API endpoint to a separate file in `pages/api/`
  - [ ] Update API calls in components to use new endpoints
  - [ ] Set up proper error handling for API routes

## 4. Static Assets and Images

- [ ] Move static assets to Next.js `public` directory
- [ ] Convert image components to use Next.js `Image` component
  - [ ] Update all `<img>` tags to use `next/image`
  - [ ] Configure proper image optimization settings
  - [ ] Update any SVG components to work with Next.js

## 5. Environment Variables

- [ ] Create `.env.local` file
- [ ] Move all environment variables from CRA to Next.js format
- [ ] Add `NEXT_PUBLIC_` prefix to client-side variables
- [ ] Update any environment variable references in the code

## 6. Styling Updates

- [x] Set up CSS Modules structure
- [x] Review and update CSS imports
- [x] Convert remaining CSS files to CSS Modules
  - [x] Convert LoadingSpinner.css to module
  - [x] Convert ReactLogo.css to module
  - [x] Convert TypeScriptLogo.css to module
  - [x] Convert Layout.css to module
  - [x] Convert remaining component CSS files
- [ ] Update any CSS-in-JS solutions to work with Next.js
- [x] Ensure global styles are properly imported in `_app.tsx`

## 7. Performance Optimizations

- [ ] Implement proper code splitting
- [ ] Set up dynamic imports where appropriate
- [ ] Configure proper caching strategies
- [ ] Implement proper loading states

## 8. Testing Updates

- [ ] Update testing configuration for Next.js
- [ ] Convert any CRA-specific tests to work with Next.js
- [ ] Set up proper testing utilities for Next.js pages
- [ ] Update any E2E tests to work with Next.js routing

## 9. Build and Deployment

- [ ] Update build scripts
- [ ] Configure proper build output
- [ ] Set up proper deployment configuration
- [ ] Test production build locally

## 10. Clean Up

- [ ] Remove CRA-specific files and configurations
- [ ] Remove unused dependencies
- [ ] Update documentation
- [ ] Clean up any console logs or debugging code

## 11. Specific Component Updates

- [ ] Update `App.tsx` to work with Next.js
- [x] Convert `Layout.tsx` to use Next.js layout system
- [ ] Update any components using browser-specific APIs
- [ ] Review and update any third-party component integrations

## 12. Documentation

- [x] Create component structure documentation
- [ ] Update README with Next.js-specific instructions
- [ ] Document new project structure
- [ ] Add deployment instructions
- [ ] Update any development guidelines

## Notes

- Keep the existing functionality while migrating
- Test each change thoroughly
- Make sure all features work in both development and production
- Consider implementing changes incrementally to maintain stability

## Resources

- [Next.js Documentation](https://nextjs.org/docs)
- [Next.js Migration Guide](https://nextjs.org/docs/migrating/from-create-react-app)
- [Next.js API Routes](https://nextjs.org/docs/api-routes/introduction)
- [Next.js Image Component](https://nextjs.org/docs/basic-features/image-optimization)
