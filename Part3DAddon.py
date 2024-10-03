from PyQt6.QtCore import QObject, QThread, pyqtSignal
    
import os
import socket
import numpy
import math
import trimesh

from UM.Extension import Extension
from cura.CuraApplication import CuraApplication

from UM.Mesh.MeshData import MeshData, calculateNormalsFromIndexedVertices
from cura.Scene.CuraSceneNode import CuraSceneNode
from cura.Scene.SliceableObjectDecorator import SliceableObjectDecorator
from cura.Scene.BuildPlateDecorator import BuildPlateDecorator
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from cura.CuraVersion import CuraVersion  # type: ignore
from UM.Logger import Logger
from http.server import HTTPServer, BaseHTTPRequestHandler

from zeroconf import IPVersion, ServiceInfo, Zeroconf, get_all_addresses

import tempfile

HOST, PORT = '0.0.0.0', 51525

class HttpDaemon(QThread):
    fileReceivedSignal = pyqtSignal(str)

    def run(self):
        self._server = HTTPServer(('', PORT), Part3DRequestHandler)
        self._server.RequestHandlerClass.setSignal(self.fileReceivedSignal)
        self._server.serve_forever()

    def stop(self):
        self._server.shutdown()
        self._server.socket.close()
        self.wait()


class Part3DRequestHandler(BaseHTTPRequestHandler):
    _signal = None  # Class-level attribute to hold the reference to the daemon

    @classmethod
    def setSignal(cls, daemon):
        cls._signal = daemon

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".stl") as f:
            f.write(post_data)
            temp_path = f.name
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"File received, file path: " + temp_path.encode())
        
        if self._signal:
            self._signal.emit(temp_path)
        else:
            Logger.log('d', "Signal not set")

    def log_message(self, format, *args):
        Logger.log('d', "RequestHandler --> " + format % args)
        return

class Part3DIntegration(QObject, Extension):
    from cura.CuraApplication import CuraApplication

    api = CuraApplication.getInstance().getCuraAPI()

    def __init__(self, parent = None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)
        
        self._controller = CuraApplication.getInstance().getController()
        self._message = None
        
        self.setMenuName("Part3D")

        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)

        desc = {'service': 'Part3D Angelo Bartolome\'s App', 'version': '0.0.1', 'path': '/Part3D', "cura_version": CuraVersion, "hostname": socket.gethostname()}
        addrs = [socket.inet_pton(socket.AF_INET, address) for address in get_all_addresses()]

        self.wsInfo = ServiceInfo(
                        '_http._tcp.local.',
                       "Part3Dapp._http._tcp.local.", 
                       addresses=addrs,
                       port=PORT,
                       properties=desc,
                       server="Part3Dapp.local."
                    )

        zeroconf.register_service(self.wsInfo)

        self.httpDaemon = HttpDaemon()
        self.httpDaemon.fileReceivedSignal.connect(self._onFileReceived)
        self.httpDaemon.start()

    def _onFileReceived(self, file_path: str) -> None:
        Logger.log('d', "File received --> " + file_path)
        self._addShape(self._toMeshData(trimesh.load(file_path)))
        os.remove(file_path)

    def _toMeshData(self, tri_node: trimesh.base.Trimesh) -> MeshData:
        tri_node.apply_transform(trimesh.transformations.rotation_matrix(math.radians(90), [-1, 0, 0]))
        tri_faces = tri_node.faces
        tri_vertices = tri_node.vertices

        indices = []
        vertices = []

        index_count = 0
        face_count = 0
        for tri_face in tri_faces:
            face = []
            for tri_index in tri_face:
                vertices.append(tri_vertices[tri_index])
                face.append(index_count)
                index_count += 1
            indices.append(face)
            face_count += 1

        vertices = numpy.asarray(vertices, dtype=numpy.float32)
        indices = numpy.asarray(indices, dtype=numpy.int32)
        normals = calculateNormalsFromIndexedVertices(vertices, indices, face_count)

        mesh_data = MeshData(vertices=vertices, indices=indices, normals=normals)
        return mesh_data

    def _addShape(self, mesh_data: MeshData) -> None:
        application = CuraApplication.getInstance()
        global_stack = application.getGlobalContainerStack()
        if not global_stack:
            return

        node = CuraSceneNode()

        node.setMeshData(mesh_data)
        node.setSelectable(True)
        node.setName("Part3DShape" + str(id(mesh_data)))

        scene = self._controller.getScene()
        existingChildren = scene.getRoot().getAllChildren()

        # remove any existing shapes that start with "Part3DShape"
        for child in existingChildren:
            if child.getName().startswith("Part3DShape"):
                scene.getRoot().removeChild(child)

        op = AddSceneNodeOperation(node, scene.getRoot())
        op.push()
        extruder_stack = application.getExtruderManager().getActiveExtruderStacks() 
        
        extruder_nr=len(extruder_stack)
        ext_pos = 0
        if ext_pos>0 and ext_pos<=extruder_nr :
            default_extruder_position = int(ext_pos-1)
        else :
            default_extruder_position = int(application.getMachineManager().defaultExtruderPosition)

        default_extruder_id = extruder_stack[default_extruder_position].getId()

        node.callDecoration("setActiveExtruder", default_extruder_id)

        active_build_plate = application.getMultiBuildPlateModel().activeBuildPlate
        node.addDecorator(BuildPlateDecorator(active_build_plate))

        node.addDecorator(SliceableObjectDecorator())

        application.getController().getScene().sceneChanged.emit(node)