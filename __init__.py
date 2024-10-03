from . import Part3DAddon

def getMetaData():
    return {}

def register(app):
    return {"extension": Part3DAddon.Part3DAddon()}
