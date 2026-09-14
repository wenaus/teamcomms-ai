"""HTTP and MCP share the same scoped topic and presentation contracts."""
from starlette.routing import Route
from teamcomms.service.dispatch import invoke
from . import operations as ops,attention
from .schemas import (CreateTopic,UpdateTopic,Topics,TopicRead,Notices,PublishNotice,ResolveDecision,FollowTopic,TopicPage,SessionRead,SetAttention,PlanAttention)


def register(mcp,endpoint):
    contracts=[('create_topic','/topics','POST',ops.create_topic,CreateTopic),
        ('update_topic','/topics/update','POST',ops.update_topic,UpdateTopic),
        ('list_topics','/topics','GET',ops.list_topics,Topics),
        ('get_topic','/topics/read','GET',ops.get_topic,TopicRead),
        ('get_topic_notices','/notices','GET',ops.get_topic_notices,Notices),
        ('publish_notice','/notices','POST',ops.publish_notice,PublishNotice),
        ('resolve_decision','/decisions','POST',ops.resolve_decision,ResolveDecision),
        ('follow_topic','/follow','POST',ops.follow_topic,FollowTopic),
        ('get_topic_conversation','/conversation','GET',ops.get_topic_conversation,TopicPage),
        ('get_attention_policy','/attention','GET',attention.get_attention_policy,SessionRead),
        ('set_attention_policy','/attention','POST',attention.set_attention_policy,SetAttention),
        ('plan_attention','/attention/plan','POST',attention.plan_attention,PlanAttention)]
    paths={}
    for name,path,method,operation,schema in contracts:
        # Set the real annotation before registration so MCP derives the request schema.
        def bind(fn,model):
            async def tool(request):return await invoke(fn,request)
            tool.__annotations__={'request':model,'return':dict}
            return tool
        mcp.tool(name=name,description=name.replace('_',' ').capitalize()+' using authenticated topic/component access; reads never acknowledge or execute work.')(bind(operation,schema))
        paths.setdefault(path,{})[method]=(operation,schema)
    return [Route('/api/capcom'+path,endpoint(contract),methods=list(contract)) for path,contract in paths.items()]
