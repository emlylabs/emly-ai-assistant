const path = require('path');
const webpack = require('webpack');
const TerserPlugin = require('terser-webpack-plugin');
const packageJson = require('./package.json');

module.exports = {
    mode: 'production',
    entry: './src/index.umd.ts',
    output: {
        path: path.resolve(__dirname, 'dist'),
        filename: 'emly-widget.js',
        library: 'ChatbotWidget',
        libraryTarget: 'umd',
        libraryExport: 'default',
        globalObject: 'this',
        clean: true,
    },
    module: {
        rules: [
            {
                test: /\.(js|jsx|ts|tsx)$/,
                exclude: /node_modules/,
                use: {
                    loader: 'babel-loader',
                    options: {
                        presets: [
                            '@babel/preset-env',
                            ['@babel/preset-react', { runtime: 'automatic' }],
                            '@babel/preset-typescript',
                        ],
                    },
                },
            },
            {
                test: /\.css$/,
                use: ['style-loader', 'css-loader'],
            },
            // Any image / font import is base64-embedded into the bundle
            // so the build output stays a single self-contained JS file.
            // If you ever need separate files, switch back to
            // `asset/resource` + a `generator.filename`.
            {
                test: /\.(png|jpe?g|gif|svg|woff|woff2|eot|ttf|otf)$/,
                type: 'asset/inline',
            },
        ],
    },
    resolve: {
        extensions: ['.ts', '.tsx', '.js', '.jsx'],
        alias: {
            'react-is': path.resolve(__dirname, 'src/shims/react-is.ts'),
        },
    },
    optimization: {
        minimize: true,
        minimizer: [
            new TerserPlugin({
                extractComments: false,
            }),
        ],
    },
    plugins: [
        new webpack.BannerPlugin({
            banner: `Chatbot Widget v${packageJson.version} | Built: ${new Date().toISOString()}`,
            raw: false,
        }),
    ],
};
