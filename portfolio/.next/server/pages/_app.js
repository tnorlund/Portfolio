/*
 * ATTENTION: An "eval-source-map" devtool has been used.
 * This devtool is neither made for production nor for readable output files.
 * It uses "eval()" calls to create a separate source file with attached SourceMaps in the browser devtools.
 * If you are trying to read the output file, select a different devtool (https://webpack.js.org/configuration/devtool/)
 * or disable the default devtool with "devtool: false".
 * If you are looking for production-ready output files, see mode: "production" (https://webpack.js.org/configuration/mode/).
 */
(() => {
var exports = {};
exports.id = "pages/_app";
exports.ids = ["pages/_app"];
exports.modules = {

/***/ "./pages/_app.tsx":
/*!************************!*\
  !*** ./pages/_app.tsx ***!
  \************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

"use strict";
eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   \"default\": () => (/* binding */ App)\n/* harmony export */ });\n/* harmony import */ var react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react/jsx-dev-runtime */ \"react/jsx-dev-runtime\");\n/* harmony import */ var react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__);\n/* harmony import */ var next_link__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! next/link */ \"./node_modules/next/link.js\");\n/* harmony import */ var next_link__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(next_link__WEBPACK_IMPORTED_MODULE_1__);\n/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! react */ \"react\");\n/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_2__);\n/* harmony import */ var _src_index_css__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ../src/index.css */ \"./src/index.css\");\n/* harmony import */ var _src_index_css__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_src_index_css__WEBPACK_IMPORTED_MODULE_3__);\n/* harmony import */ var _src_App_css__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ../src/App.css */ \"./src/App.css\");\n/* harmony import */ var _src_App_css__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_src_App_css__WEBPACK_IMPORTED_MODULE_4__);\n/* harmony import */ var _src_Receipt_css__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ../src/Receipt.css */ \"./src/Receipt.css\");\n/* harmony import */ var _src_Receipt_css__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_src_Receipt_css__WEBPACK_IMPORTED_MODULE_5__);\n\n\n\n\n\n\n// Error boundary component\nclass ErrorBoundary extends (react__WEBPACK_IMPORTED_MODULE_2___default().Component) {\n    constructor(props){\n        super(props);\n        this.state = {\n            hasError: false,\n            error: null\n        };\n    }\n    static getDerivedStateFromError(error) {\n        return {\n            hasError: true,\n            error\n        };\n    }\n    componentDidCatch(error, errorInfo) {\n        console.error(\"Error caught by boundary:\", error, errorInfo);\n    }\n    render() {\n        if (this.state.hasError) {\n            return /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"div\", {\n                style: {\n                    padding: \"20px\",\n                    textAlign: \"center\"\n                },\n                children: [\n                    /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"h2\", {\n                        children: \"Something went wrong\"\n                    }, void 0, false, {\n                        fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                        lineNumber: 30,\n                        columnNumber: 11\n                    }, this),\n                    /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"details\", {\n                        style: {\n                            whiteSpace: \"pre-wrap\"\n                        },\n                        children: this.state.error && this.state.error.toString()\n                    }, void 0, false, {\n                        fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                        lineNumber: 31,\n                        columnNumber: 11\n                    }, this)\n                ]\n            }, void 0, true, {\n                fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                lineNumber: 29,\n                columnNumber: 9\n            }, this);\n        }\n        return this.props.children;\n    }\n}\nfunction App({ Component, pageProps }) {\n    return /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(ErrorBoundary, {\n        children: /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(react__WEBPACK_IMPORTED_MODULE_2__.Suspense, {\n            fallback: /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"div\", {\n                children: \"Loading...\"\n            }, void 0, false, {\n                fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                lineNumber: 45,\n                columnNumber: 27\n            }, void 0),\n            children: /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"div\", {\n                children: [\n                    /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"header\", {\n                        children: /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(\"h1\", {\n                            children: /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)((next_link__WEBPACK_IMPORTED_MODULE_1___default()), {\n                                href: \"/\",\n                                children: \"Tyler Norlund\"\n                            }, void 0, false, {\n                                fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                                lineNumber: 49,\n                                columnNumber: 15\n                            }, this)\n                        }, void 0, false, {\n                            fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                            lineNumber: 48,\n                            columnNumber: 13\n                        }, this)\n                    }, void 0, false, {\n                        fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                        lineNumber: 47,\n                        columnNumber: 11\n                    }, this),\n                    /*#__PURE__*/ (0,react_jsx_dev_runtime__WEBPACK_IMPORTED_MODULE_0__.jsxDEV)(Component, {\n                        ...pageProps\n                    }, void 0, false, {\n                        fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                        lineNumber: 52,\n                        columnNumber: 11\n                    }, this)\n                ]\n            }, void 0, true, {\n                fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n                lineNumber: 46,\n                columnNumber: 9\n            }, this)\n        }, void 0, false, {\n            fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n            lineNumber: 45,\n            columnNumber: 7\n        }, this)\n    }, void 0, false, {\n        fileName: \"/Users/tnorlund/GitHub/example/portfolio/pages/_app.tsx\",\n        lineNumber: 44,\n        columnNumber: 5\n    }, this);\n}\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiLi9wYWdlcy9fYXBwLnRzeCIsIm1hcHBpbmdzIjoiOzs7Ozs7Ozs7Ozs7Ozs7OztBQUM2QjtBQUNXO0FBQ2Q7QUFDRjtBQUNJO0FBRTVCLDJCQUEyQjtBQUMzQixNQUFNRyxzQkFBc0JGLHdEQUFlO0lBSXpDSSxZQUFZQyxLQUFvQyxDQUFFO1FBQ2hELEtBQUssQ0FBQ0E7UUFDTixJQUFJLENBQUNDLEtBQUssR0FBRztZQUFFQyxVQUFVO1lBQU9DLE9BQU87UUFBSztJQUM5QztJQUVBLE9BQU9DLHlCQUF5QkQsS0FBWSxFQUFFO1FBQzVDLE9BQU87WUFBRUQsVUFBVTtZQUFNQztRQUFNO0lBQ2pDO0lBRUFFLGtCQUFrQkYsS0FBWSxFQUFFRyxTQUEwQixFQUFFO1FBQzFEQyxRQUFRSixLQUFLLENBQUMsNkJBQTZCQSxPQUFPRztJQUNwRDtJQUVBRSxTQUFTO1FBQ1AsSUFBSSxJQUFJLENBQUNQLEtBQUssQ0FBQ0MsUUFBUSxFQUFFO1lBQ3ZCLHFCQUNFLDhEQUFDTztnQkFBSUMsT0FBTztvQkFBRUMsU0FBUztvQkFBUUMsV0FBVztnQkFBUzs7a0NBQ2pELDhEQUFDQztrQ0FBRzs7Ozs7O2tDQUNKLDhEQUFDQzt3QkFBUUosT0FBTzs0QkFBRUssWUFBWTt3QkFBVztrQ0FDdEMsSUFBSSxDQUFDZCxLQUFLLENBQUNFLEtBQUssSUFBSSxJQUFJLENBQUNGLEtBQUssQ0FBQ0UsS0FBSyxDQUFDYSxRQUFROzs7Ozs7Ozs7Ozs7UUFJdEQ7UUFFQSxPQUFPLElBQUksQ0FBQ2hCLEtBQUssQ0FBQ2lCLFFBQVE7SUFDNUI7QUFDRjtBQUVlLFNBQVNDLElBQUksRUFBRXBCLFNBQVMsRUFBRXFCLFNBQVMsRUFBWTtJQUM1RCxxQkFDRSw4REFBQ3RCO2tCQUNDLDRFQUFDRCwyQ0FBUUE7WUFBQ3dCLHdCQUFVLDhEQUFDWDswQkFBSTs7Ozs7O3NCQUN2Qiw0RUFBQ0E7O2tDQUNDLDhEQUFDWTtrQ0FDQyw0RUFBQ0M7c0NBQ0MsNEVBQUM1QixrREFBSUE7Z0NBQUM2QixNQUFLOzBDQUFJOzs7Ozs7Ozs7Ozs7Ozs7O2tDQUduQiw4REFBQ3pCO3dCQUFXLEdBQUdxQixTQUFTOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7O0FBS2xDIiwic291cmNlcyI6WyJ3ZWJwYWNrOi8vcG9ydGZvbGlvLy4vcGFnZXMvX2FwcC50c3g/MmZiZSJdLCJzb3VyY2VzQ29udGVudCI6WyJpbXBvcnQgdHlwZSB7IEFwcFByb3BzIH0gZnJvbSBcIm5leHQvYXBwXCI7XG5pbXBvcnQgTGluayBmcm9tIFwibmV4dC9saW5rXCI7XG5pbXBvcnQgUmVhY3QsIHsgU3VzcGVuc2UgfSBmcm9tIFwicmVhY3RcIjtcbmltcG9ydCBcIi4uL3NyYy9pbmRleC5jc3NcIjtcbmltcG9ydCBcIi4uL3NyYy9BcHAuY3NzXCI7XG5pbXBvcnQgXCIuLi9zcmMvUmVjZWlwdC5jc3NcIjtcblxuLy8gRXJyb3IgYm91bmRhcnkgY29tcG9uZW50XG5jbGFzcyBFcnJvckJvdW5kYXJ5IGV4dGVuZHMgUmVhY3QuQ29tcG9uZW50PFxuICB7IGNoaWxkcmVuOiBSZWFjdC5SZWFjdE5vZGUgfSxcbiAgeyBoYXNFcnJvcjogYm9vbGVhbjsgZXJyb3I6IEVycm9yIHwgbnVsbCB9XG4+IHtcbiAgY29uc3RydWN0b3IocHJvcHM6IHsgY2hpbGRyZW46IFJlYWN0LlJlYWN0Tm9kZSB9KSB7XG4gICAgc3VwZXIocHJvcHMpO1xuICAgIHRoaXMuc3RhdGUgPSB7IGhhc0Vycm9yOiBmYWxzZSwgZXJyb3I6IG51bGwgfTtcbiAgfVxuXG4gIHN0YXRpYyBnZXREZXJpdmVkU3RhdGVGcm9tRXJyb3IoZXJyb3I6IEVycm9yKSB7XG4gICAgcmV0dXJuIHsgaGFzRXJyb3I6IHRydWUsIGVycm9yIH07XG4gIH1cblxuICBjb21wb25lbnREaWRDYXRjaChlcnJvcjogRXJyb3IsIGVycm9ySW5mbzogUmVhY3QuRXJyb3JJbmZvKSB7XG4gICAgY29uc29sZS5lcnJvcihcIkVycm9yIGNhdWdodCBieSBib3VuZGFyeTpcIiwgZXJyb3IsIGVycm9ySW5mbyk7XG4gIH1cblxuICByZW5kZXIoKSB7XG4gICAgaWYgKHRoaXMuc3RhdGUuaGFzRXJyb3IpIHtcbiAgICAgIHJldHVybiAoXG4gICAgICAgIDxkaXYgc3R5bGU9e3sgcGFkZGluZzogXCIyMHB4XCIsIHRleHRBbGlnbjogXCJjZW50ZXJcIiB9fT5cbiAgICAgICAgICA8aDI+U29tZXRoaW5nIHdlbnQgd3Jvbmc8L2gyPlxuICAgICAgICAgIDxkZXRhaWxzIHN0eWxlPXt7IHdoaXRlU3BhY2U6IFwicHJlLXdyYXBcIiB9fT5cbiAgICAgICAgICAgIHt0aGlzLnN0YXRlLmVycm9yICYmIHRoaXMuc3RhdGUuZXJyb3IudG9TdHJpbmcoKX1cbiAgICAgICAgICA8L2RldGFpbHM+XG4gICAgICAgIDwvZGl2PlxuICAgICAgKTtcbiAgICB9XG5cbiAgICByZXR1cm4gdGhpcy5wcm9wcy5jaGlsZHJlbjtcbiAgfVxufVxuXG5leHBvcnQgZGVmYXVsdCBmdW5jdGlvbiBBcHAoeyBDb21wb25lbnQsIHBhZ2VQcm9wcyB9OiBBcHBQcm9wcykge1xuICByZXR1cm4gKFxuICAgIDxFcnJvckJvdW5kYXJ5PlxuICAgICAgPFN1c3BlbnNlIGZhbGxiYWNrPXs8ZGl2PkxvYWRpbmcuLi48L2Rpdj59PlxuICAgICAgICA8ZGl2PlxuICAgICAgICAgIDxoZWFkZXI+XG4gICAgICAgICAgICA8aDE+XG4gICAgICAgICAgICAgIDxMaW5rIGhyZWY9XCIvXCI+VHlsZXIgTm9ybHVuZDwvTGluaz5cbiAgICAgICAgICAgIDwvaDE+XG4gICAgICAgICAgPC9oZWFkZXI+XG4gICAgICAgICAgPENvbXBvbmVudCB7Li4ucGFnZVByb3BzfSAvPlxuICAgICAgICA8L2Rpdj5cbiAgICAgIDwvU3VzcGVuc2U+XG4gICAgPC9FcnJvckJvdW5kYXJ5PlxuICApO1xufVxuIl0sIm5hbWVzIjpbIkxpbmsiLCJSZWFjdCIsIlN1c3BlbnNlIiwiRXJyb3JCb3VuZGFyeSIsIkNvbXBvbmVudCIsImNvbnN0cnVjdG9yIiwicHJvcHMiLCJzdGF0ZSIsImhhc0Vycm9yIiwiZXJyb3IiLCJnZXREZXJpdmVkU3RhdGVGcm9tRXJyb3IiLCJjb21wb25lbnREaWRDYXRjaCIsImVycm9ySW5mbyIsImNvbnNvbGUiLCJyZW5kZXIiLCJkaXYiLCJzdHlsZSIsInBhZGRpbmciLCJ0ZXh0QWxpZ24iLCJoMiIsImRldGFpbHMiLCJ3aGl0ZVNwYWNlIiwidG9TdHJpbmciLCJjaGlsZHJlbiIsIkFwcCIsInBhZ2VQcm9wcyIsImZhbGxiYWNrIiwiaGVhZGVyIiwiaDEiLCJocmVmIl0sInNvdXJjZVJvb3QiOiIifQ==\n//# sourceURL=webpack-internal:///./pages/_app.tsx\n");

/***/ }),

/***/ "./src/App.css":
/*!*********************!*\
  !*** ./src/App.css ***!
  \*********************/
/***/ (() => {



/***/ }),

/***/ "./src/Receipt.css":
/*!*************************!*\
  !*** ./src/Receipt.css ***!
  \*************************/
/***/ (() => {



/***/ }),

/***/ "./src/index.css":
/*!***********************!*\
  !*** ./src/index.css ***!
  \***********************/
/***/ (() => {



/***/ }),

/***/ "next/dist/compiled/next-server/pages.runtime.dev.js":
/*!**********************************************************************!*\
  !*** external "next/dist/compiled/next-server/pages.runtime.dev.js" ***!
  \**********************************************************************/
/***/ ((module) => {

"use strict";
module.exports = require("next/dist/compiled/next-server/pages.runtime.dev.js");

/***/ }),

/***/ "react":
/*!************************!*\
  !*** external "react" ***!
  \************************/
/***/ ((module) => {

"use strict";
module.exports = require("react");

/***/ }),

/***/ "react/jsx-dev-runtime":
/*!****************************************!*\
  !*** external "react/jsx-dev-runtime" ***!
  \****************************************/
/***/ ((module) => {

"use strict";
module.exports = require("react/jsx-dev-runtime");

/***/ }),

/***/ "react/jsx-runtime":
/*!************************************!*\
  !*** external "react/jsx-runtime" ***!
  \************************************/
/***/ ((module) => {

"use strict";
module.exports = require("react/jsx-runtime");

/***/ })

};
;

// load runtime
var __webpack_require__ = require("../webpack-runtime.js");
__webpack_require__.C(exports);
var __webpack_exec__ = (moduleId) => (__webpack_require__(__webpack_require__.s = moduleId))
var __webpack_exports__ = __webpack_require__.X(0, ["vendor-chunks/next","vendor-chunks/@swc"], () => (__webpack_exec__("./pages/_app.tsx")));
module.exports = __webpack_exports__;

})();