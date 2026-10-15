import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'

const TEXT_COMPONENTS: Components = {
  a: ({ children }) => <span>{children}</span>,
}

/** Allow textual Markdown only: no embedded HTML, remote images or executable links. */
export function AssistantAnswer({ text }: { text: string }) {
  return (
    <div className="text-[19px] leading-[1.5] [overflow-wrap:anywhere] [&_p]:mb-3 [&_p:last-child]:mb-0 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-6 [&_li]:my-1 [&_pre]:overflow-x-auto [&_pre]:bg-raised [&_pre]:p-3 [&_code]:font-mono [&_code]:text-id [&_blockquote]:border-l [&_blockquote]:border-line-strong [&_blockquote]:pl-4 [&_h1]:mb-3 [&_h1]:text-panel-title [&_h2]:mb-3 [&_h2]:text-panel-title [&_h3]:mb-3 [&_h3]:font-semibold">
      <Markdown skipHtml disallowedElements={['img']} components={TEXT_COMPONENTS}>
        {text}
      </Markdown>
    </div>
  )
}
