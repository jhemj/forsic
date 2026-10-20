import { expect, it } from 'vitest'
import { configuredConnection } from './forsic-connection'

it('shows the full saved model and removes credentials and tokens from server URLs', () => {
  expect(configuredConnection({llm_current:{model:'provider/org/full-model',base_url:'https://name:secret@host.test:8443/v1?token=secret#secret'}}))
    .toEqual({model:'provider/org/full-model',server:'https://host.test:8443/v1',pending:false})
  expect(configuredConnection({llm_current:{model:'model',base_url:'secret invalid URL'}}).server).toBe('')
  expect(configuredConnection({llm_current:{model:'model',base_url:'javascript:secret'}}).server).toBe('')
  expect(configuredConnection({llm_current:{model:'active/model'},llm_pending:true})).toEqual({model:'active/model',server:'',pending:true})
})
