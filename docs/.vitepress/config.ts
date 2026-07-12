import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Kratos MCP Server',
  description:
    'MCP server for Kratos Multiphysics: scaffold, run and post-process finite element simulations from AI assistants',
  base: process.env.DOCS_BASE ?? '/',
  themeConfig: {
    nav: [
      { text: 'Guide', link: '/guide/getting-started' },
      { text: 'Tools', link: '/tools/' },
      { text: 'Tutorials', link: '/tutorials/cantilever-beam' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: 'Guide',
          items: [
            { text: 'Getting started', link: '/guide/getting-started' },
            { text: 'Installation', link: '/guide/installation' },
            { text: 'Connecting a client', link: '/guide/connecting' },
            { text: 'Architecture', link: '/guide/architecture' },
            { text: 'The MDPA mesh format', link: '/guide/mdpa-format' },
            { text: 'Troubleshooting', link: '/guide/troubleshooting' },
          ],
        },
      ],
      '/tools/': [
        {
          text: 'Tool reference',
          items: [
            { text: 'Overview', link: '/tools/' },
            { text: 'Environment & introspection', link: '/tools/environment' },
            { text: 'Project scaffolding', link: '/tools/scaffolding' },
            { text: 'Meshes (MDPA)', link: '/tools/mesh' },
            { text: 'Simulation & jobs', link: '/tools/simulation' },
            { text: 'Post-processing', link: '/tools/postprocessing' },
            { text: 'Resources', link: '/tools/resources' },
            { text: 'Prompts', link: '/tools/prompts' },
          ],
        },
      ],
      '/tutorials/': [
        {
          text: 'Tutorials',
          items: [
            { text: 'Cantilever beam', link: '/tutorials/cantilever-beam' },
            { text: 'Thermal bar', link: '/tutorials/thermal-bar' },
          ],
        },
      ],
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/KratosMultiphysics/Kratos' },
    ],
    search: { provider: 'local' },
    outline: [2, 3],
  },
})
